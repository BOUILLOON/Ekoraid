"""Deploy the isolated schema and guest search with a securely injected account token.

The account token is read only from SUPABASE_ACCESS_TOKEN. No passwords, tokens,
CLI command lines containing secrets, or remote response bodies are logged.
"""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

HERE=Path(__file__).resolve().parent
CLI_VERSION='2.120.0'
SCHEMA_FILES=('supabase.sql','categories.sql')
EXPECTED_FUNCTIONS=(
    'playlist25_session','playlist25_propose','playlist25_vote','playlist25_withdraw',
    'playlist25_profile','playlist25_avatars','playlist25_snapshot','playlist25_admin_publish',
    'playlist25_list','playlist25_public_item',
    'playlist25_organizer_id','playlist25_approve_organizer','playlist25_organizer_session',
    'playlist25_categories_catalogue','playlist25_categories_list','playlist25_category_suggestions',
    'playlist25_category_ids_valid','playlist25_admin_categories','playlist25_organizer_category',
    'playlist25_proposal_categories','playlist25_organizer_proposal_categories',
)

class DeployError(ValueError): pass
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


def project_ref():
    config=json.loads((HERE/'public-config.json').read_text(encoding='utf-8'))
    match=re.fullmatch(r'https://([a-z0-9]{20})\.supabase\.co/?',config['url'])
    if not match: raise DeployError('Configurez une URL de projet Supabase valide dans cloud/public-config.json.')
    return match[1]


def management_request(ref,method,suffix,body=None):
    token=os.environ.get('SUPABASE_ACCESS_TOKEN')
    if not token or '\n' in token or '\r' in token:
        raise DeployError('SUPABASE_ACCESS_TOKEN absent. Ajoutez le jeton dans les réglages sécurisés de l’environnement, jamais dans le dépôt ou le chat.')
    url=f'https://api.supabase.com/v1/projects/{ref}'+suffix
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    request=Request(url,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
    try:
        with build_opener(NoRedirect()).open(request,timeout=60) as response:
            raw=response.read(4*1024*1024)
    except HTTPError as error:
        if error.code in (401,403): raise DeployError('Accès de déploiement refusé. Vérifiez le jeton sécurisé, ses droits sur ce projet et les domaines réseau autorisés.') from None
        raise DeployError(f'Supabase a refusé cette étape (HTTP {error.code}). Les réponses privées ne sont pas affichées.') from None
    except (URLError,TimeoutError,OSError):
        raise DeployError('Connexion au service de déploiement Supabase impossible. Vérifiez le réseau puis réessayez.') from None
    if not raw: return None
    try: return json.loads(raw)
    except ValueError: raise DeployError('Réponse inattendue du service de déploiement Supabase.') from None


def _migration_body(path):
    """Remove only documented outer BEGIN/COMMIT lines, never function bodies.

    Each checked-in migration starts/ends its SQL with standalone begin;/commit;.
    Blank lines and full-line -- comments may surround those two statements. A
    changed wrapper is rejected before any network call rather than guessed.
    """
    lines=path.read_text(encoding='utf-8').splitlines(keepends=True)
    statements=[index for index,line in enumerate(lines) if line.strip() and not line.lstrip().startswith('--')]
    if len(statements)<2 or lines[statements[0]].strip().lower()!='begin;' or lines[statements[-1]].strip().lower()!='commit;':
        raise DeployError(f'Le script {path.name} doit conserver ses instructions externes begin; et commit; séparées.')
    for index in (statements[0],statements[-1]): lines[index]=''
    return ''.join(lines).rstrip()+'\n'


def compose_schema_sql():
    """Apply base then categories atomically; retain whitelist, IDs and history."""
    bodies=[f'-- Migration : {name}\n'+_migration_body(HERE/name) for name in SCHEMA_FILES]
    return 'begin;\n\n'+'\n'.join(bodies)+"\nnotify pgrst, 'reload schema';\ncommit;\n"


def wait_for_schema_cache(ref):
    """Bounded, anonymous read probe: no account, guest, or collection mutation."""
    config=json.loads((HERE/'public-config.json').read_text(encoding='utf-8'))
    key=config.get('publicKey')
    headers={'Content-Type':'application/json'}
    if isinstance(key,str) and key.startswith('sb_publishable_'):
        headers['apikey']=key
    else:
        try:
            payload=key.split('.')[1]
            claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
            if claims.get('role')!='anon': raise ValueError()
        except (AttributeError,IndexError,TypeError,ValueError):
            raise DeployError('La vérification publique nécessite seulement une clé publishable ou anon valide.') from None
        headers.update(apikey=key,Authorization='Bearer '+key)
    request=Request(f'https://{ref}.supabase.co/rest/v1/rpc/playlist25_organizer_session',
                    data=b'{}',headers=headers,method='POST')
    for attempt in range(6):
        try:
            with build_opener(NoRedirect()).open(request,timeout=5) as response:
                data=json.loads(response.read(16*1024))
            if isinstance(data,dict) and data.get('authenticated') is False and data.get('organizer') is None:
                return
            raise DeployError('La vérification anonyme de l’espace organisation a échoué ; aucune réponse privée n’est affichée.')
        except HTTPError as error:
            if error.code!=404:
                raise DeployError(f'La vérification publique Supabase a été refusée (HTTP {error.code}). Vérifiez sa clé publique.') from None
        except (URLError,TimeoutError,OSError,ValueError):
            pass
        if attempt<5: time.sleep(.25)
    raise DeployError('Le schéma SQL est installé, mais la nouvelle RPC publique n’est pas encore confirmée. Attendez le rechargement du cache Supabase et vérifiez le réseau avant de publier Pages.')


def install_schema(ref):
    sql=compose_schema_sql()
    management_request(ref,'POST','/database/query',{'query':sql,'read_only':False})
    # Only return counts, never guest data or account keys.
    names=','.join("'"+name+"'" for name in EXPECTED_FUNCTIONS)
    query="SELECT count(*) AS functions,count(DISTINCT p.proname) AS distinct_functions FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname IN ("+names+")"
    verified=management_request(ref,'POST','/database/query',{'query':query,'read_only':True})
    def count(value):
        if type(value) is int:return value
        if isinstance(value,str) and re.fullmatch(r'[0-9]{1,8}',value):return int(value)
        return None
    if not isinstance(verified,list) or len(verified)!=1 or not isinstance(verified[0],dict) or any(
            count(verified[0].get(key))!=len(EXPECTED_FUNCTIONS) for key in ('functions','distinct_functions')):
        raise DeployError('La vérification des fonctions attendues a échoué ; aucune readiness n’est revendiquée.')
    wait_for_schema_cache(ref)
    print('Schéma, catégories et permissions Supabase installés et vérifiés.')


def deploy_search(ref,cli=None):
    if not os.environ.get('SUPABASE_ACCESS_TOKEN'): raise DeployError('SUPABASE_ACCESS_TOKEN absent.')
    if cli:
        command=[str(cli)]
    elif shutil.which('supabase'):
        command=['supabase']
    elif shutil.which('npx'):
        command=['npx','--yes',f'supabase@{CLI_VERSION}']
    else: raise DeployError('Installez Node.js (npx) ou le CLI officiel Supabase pour déployer la recherche.')
    environment=os.environ.copy()
    environment.setdefault('SUPABASE_HOME',str(HERE.parent/'data'/'supabase-cli'))
    environment.setdefault('npm_config_cache',str(HERE.parent/'data'/'npm-cache'))
    environment['SUPABASE_TELEMETRY_DISABLED']='true'
    try:
        result=subprocess.run(command+['functions','deploy','music-search','--project-ref',ref,'--use-api','--workdir',str(HERE),'--agent','yes','--log-level','warn'],env=environment,capture_output=True,text=True,timeout=180)
    except (OSError,subprocess.TimeoutExpired):
        raise DeployError('Le CLI Supabase n’a pas terminé le déploiement. Vérifiez son installation et le réseau.') from None
    if result.returncode:
        raise DeployError('Le CLI Supabase a refusé le déploiement de la recherche. Vérifiez le jeton sécurisé et les droits du projet ; aucune réponse privée n’est affichée.')
    functions=management_request(ref,'GET','/functions')
    if not isinstance(functions,list) or not any(item.get('slug')=='music-search' for item in functions):
        raise DeployError('La fonction music-search n’est pas confirmée sur le projet.')
    print('Fonction de recherche déployée et confirmée. Son accès actuel à YouTube reste à tester séparément.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sql-only',action='store_true')
    parser.add_argument('--functions-only',action='store_true')
    parser.add_argument('--supabase-cli',type=Path,help='Chemin vers le CLI officiel, optionnel')
    args=parser.parse_args()
    if args.sql_only and args.functions_only: parser.error('Choisissez un seul filtre.')
    try:
        ref=project_ref()
        if not args.functions_only: install_schema(ref)
        if not args.sql_only: deploy_search(ref,args.supabase_cli)
    except (DeployError,OSError,ValueError) as error:
        print(str(error),file=sys.stderr)
        return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
