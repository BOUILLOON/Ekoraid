"""Grouped CI errors retain distinct causes and publish only aggregate counts."""
import importlib.util
import json
from pathlib import Path
from xml.etree import ElementTree as ET

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/packaging/annotate_pytest.py'
spec=importlib.util.spec_from_file_location('pytest_annotations',SCRIPT)
annotations=importlib.util.module_from_spec(spec);spec.loader.exec_module(annotations)

def test_repeated_overflow_does_not_hide_other_errors_and_summary_has_no_fixtures(tmp_path,capsys):
    root=ET.Element('testsuites');suite=ET.SubElement(root,'testsuite',time='12.5')
    ET.SubElement(suite,'testcase',name='passed-secret-fixture-name',time='1')
    skipped=ET.SubElement(suite,'testcase',name='skipped-case');ET.SubElement(skipped,'skipped')
    for index in range(15):
        case=ET.SubElement(suite,'testcase',name=f'overflow_{index}')
        failure=ET.SubElement(case,'failure',message='OverflowError: signed64 exceeded')
        failure.text=f'private-fixture-value-{index}\nE OverflowError: signed64 exceeded'
    error=ET.SubElement(suite,'testcase',name='different_error')
    ET.SubElement(error,'error',message='PermissionError: denied').text='complete distinct diagnostic'
    report=tmp_path/'pytest.xml';ET.ElementTree(root).write(report,encoding='utf-8')
    annotations.annotate(report)
    output=capsys.readouterr().out.splitlines()
    assert output[0].startswith('::notice title=Windows pytest summary::')
    assert len([line for line in output if line.startswith('::error')])==2
    assert '15 failure/error occurrence(s)' in output[1]
    assert 'private-fixture-value-0' in output[1] and 'private-fixture-value-14' not in output[1]
    assert 'PermissionError' in output[2] and 'complete distinct diagnostic' in output[2]
    content=(tmp_path/'pytest-summary.json').read_text(encoding='utf-8')
    assert 'fixture' not in content and 'PermissionError' not in content and 'overflow' not in content
    assert json.loads(content)=={'report_available':True,'tests':18,'passed':1,'failed':15,'errors':1,'skipped':1,
                               'duration_seconds':12.5,'failure_groups':2}

def test_message_fallback_groups_tracebacks_without_message_attribute(tmp_path,capsys):
    root=ET.Element('testsuite',time='2')
    for index in range(2):
        case=ET.SubElement(root,'testcase',name=f'case{index}')
        ET.SubElement(case,'failure').text=f'varying fixture {index}\nE       OverflowError: same error\nfile{index}.py:10: OverflowError'
    report=tmp_path/'pytest.xml';ET.ElementTree(root).write(report)
    annotations.annotate(report)
    assert capsys.readouterr().out.count('::error')==1
    assert json.loads((tmp_path/'pytest-summary.json').read_text())['failure_groups']==1

def test_complete_representative_and_escaping_are_preserved(tmp_path,capsys):
    root=ET.Element('testsuite',time='nan');case=ET.SubElement(root,'testcase',name='representative')
    details='fixture%value\r\n'+('x'*13000)+'complete-tail'
    ET.SubElement(case,'failure',message='ValueError: comma,value%').text=details
    report=tmp_path/'pytest.xml';ET.ElementTree(root).write(report)
    annotations.annotate(report)
    output=capsys.readouterr().out
    assert 'ValueError%3A comma%2Cvalue%25' in output
    assert 'fixture%25value%0A' in output and 'complete-tail' in output
    assert annotations.escape_message('x\r\ny%')=='x%0D%0Ay%25'
    assert json.loads((tmp_path/'pytest-summary.json').read_text())['duration_seconds']==0

def test_nested_suite_statistics_are_not_counted_twice():
    root=ET.fromstring('<testsuites><testsuite time="4"><testsuite time="4"><testcase time="3"/></testsuite></testsuite></testsuites>')
    summary,groups=annotations.summarize(root)
    assert summary['tests']==summary['passed']==1 and summary['duration_seconds']==4 and not groups

def test_missing_and_malformed_report_never_claim_zero_failures(tmp_path,capsys):
    report=tmp_path/'nested'/'pytest.xml'
    annotations.annotate(report)
    assert 'did not produce' in capsys.readouterr().out
    summary=json.loads(report.with_name('pytest-summary.json').read_text())
    assert summary['report_available'] is False and summary['failed'] is None
    report.write_text('<malformed secret-fixture',encoding='utf-8')
    annotations.annotate(report)
    assert 'unreadable' in capsys.readouterr().out
    assert 'secret-fixture' not in report.with_name('pytest-summary.json').read_text()

def test_passing_empty_report_and_case_duration_fallback(tmp_path,capsys):
    root=ET.fromstring('<testsuite><testcase time="1.25"/><testcase time="0.5"/></testsuite>')
    report=tmp_path/'pytest.xml';ET.ElementTree(root).write(report)
    annotations.annotate(report)
    assert '::error' not in capsys.readouterr().out
    assert json.loads((tmp_path/'pytest-summary.json').read_text())['duration_seconds']==1.75

def test_workflow_copies_and_publishes_count_summary_with_original_release_assets():
    text=(SCRIPT.parents[2]/'.github/workflows/windows-studio.yml').read_text(encoding='utf-8')
    assert 'Copy-Item build/windows/pytest-summary.json dist/windows/pytest-summary.json' in text
    assert 'build/windows/pytest.xml' in text and 'build/windows/pytest-summary.json' in text
    create=text.split('gh release create $env:RELEASE_TAG',1)[1]
    for name in ['Studio-Fanny-Windows.zip','Studio-Fanny-Windows.zip.sha256','build-info.json','windows-smoke.json','pytest-summary.json']:
        assert f'dist/windows/{name} `' in create
