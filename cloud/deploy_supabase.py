"""Deploy the isolated schema and guest search with a securely injected account token.

The account token is read only from SUPABASE_ACCESS_TOKEN. No passwords, tokens,
CLI command lines containing secrets, or remote response bodies are logged.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

HERE=Path(__file__).resolve().parent
CLI_VERSION='2.120.0'

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


def install_schema(ref):
    sql=(HERE/'supabase.sql').read_text(encoding='utf-8')
    management_request(ref,'POST','/database/query',{'query':sql,'read_only':False})
    # Only return counts, never guest data or account keys.
    verified=management_request(ref,'POST','/database/query',{'query':"SELECT count(*) AS functions FROM pg_proc WHERE proname IN ('playlist25_session','playlist25_propose','playlist25_vote','playlist25_snapshot','playlist25_admin_publish')",'read_only':True})
    if not isinstance(verified,list) or not verified or int(verified[0].get('functions',0))!=5:
        raise DeployError('La vérification des fonctions attendues a échoué ; aucune readiness n’est revendiquée.')
    print('Schéma et permissions Supabase installés et vérifiés.')


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
