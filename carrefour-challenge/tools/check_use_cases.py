"""Require every declared persona/component case to appear green in actual JUnit."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

def check(junit):
    matrix = json.loads(Path('/app/docs/use-cases.json').read_text())
    expected = {(component, persona) for component in matrix['components'] for persona in matrix['personas']}
    cases = matrix['cases']
    assert len({case['id'] for case in cases}) == len(cases)
    assert {(case['component'], case['persona']) for case in cases} == expected
    tree = ET.parse(junit)
    executed = {}
    for row in tree.iter('testcase'):
        key = row.attrib['classname'].split('.')[-1]+'.py::'+row.attrib['name'].split('[')[0]
        executed.setdefault(key, []).append(not any(row.find(tag) is not None for tag in ('failure', 'error', 'skipped')))
    result = []
    for case in cases:
        assert case['expected'] and case['forbidden'] and case['tests']
        missing = [name for name in case['tests'] if name not in executed or not all(executed[name])]
        result.append({'id': case['id'], 'component': case['component'], 'persona': case['persona'],
                       'passed': not missing, 'missing_or_failed': missing})
    return {'complete': all(row['passed'] for row in result), 'declared_cases': len(result),
            'components': len(matrix['components']), 'personas': len(matrix['personas']), 'cases': result,
            'boundary': matrix['boundary']}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--junit', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = check(args.junit)
    Path(args.output).write_text(json.dumps(result, indent=2))
    print(json.dumps({key: value for key, value in result.items() if key != 'cases'}))
    if not result['complete']: raise SystemExit(1)

if __name__ == '__main__': main()
