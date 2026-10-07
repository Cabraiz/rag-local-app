"""The agent image runs the CLI only; the test tools and the tests live in the `test` stage."""
import re
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV_TOOLS = ('pytest', 'pytest-cov', 'coverage', 'ruff', 'mypy', 'pytest-xdist', 'execnet')


def stages():
    """{stage name: (base, its instructions)} of the Dockerfile."""
    found, name = {}, None
    for line in (ROOT / 'Dockerfile').read_text(encoding='utf-8').splitlines():
        start = re.match(r'FROM (\S+) AS (\S+)', line)
        if start:
            name = start[2]
            found[name] = (start[1], [])
        elif name and line.strip() and not line.startswith('#'):
            found[name][1].append(line)
    return {stage: (base, '\n'.join(lines)) for stage, (base, lines) in found.items()}


def pins(name):
    return dict(re.findall(r'^([A-Za-z0-9_.-]+)==(\S+)$', (ROOT / name).read_text(encoding='utf-8'), re.M))


def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def test_the_agent_stage_has_no_test_tools_tests_or_ocr_engine():
    base, agent = stages()['agent']
    assert base == 'base'  # not the Tesseract stage
    assert 'COPY --chown=app:app . .' not in agent and 'tests' not in agent
    assert 'requirements-dev' not in agent and not any(re.search(rf'\b{tool}\b', agent) for tool in DEV_TOOLS)


def test_the_test_stage_is_the_agent_plus_the_dev_tools_and_the_project():
    base, test = stages()['test']
    assert base == 'agent'
    assert '-r requirements.txt -r requirements-dev.txt' in test and 'COPY --chown=app:app . .' in test
    assert 'tesseract-ocr-por' in test


def test_runtime_and_dev_requirements_are_split_and_pinned():
    runtime, dev = pins('requirements.txt'), pins('requirements-dev.txt')
    assert not set(DEV_TOOLS) & set(runtime)
    assert set(dev) == set(DEV_TOOLS)
    assert (ROOT / 'requirements-dev.txt').read_text(encoding='utf-8').count('-c requirements.txt') == 1


def test_every_stage_installs_with_the_pins_of_the_transitive_dependencies():
    for stage, (_, instructions) in stages().items():
        installs = re.findall(r'pip install .*', instructions)
        assert all('-c constraints.txt' in line for line in installs), stage
    assert 'COPY requirements.txt constraints.txt ./' in stages()['base'][1]


def test_every_package_of_the_test_image_is_pinned_once_at_its_installed_version():
    """The test image has every runtime dependency: what pip installed there is exactly what the three files pin,
    and constraints.txt repeats no direct pin (a bump in requirements*.txt would then conflict with itself)."""
    direct = {normalized(name) for name in [*pins('requirements.txt'), *pins('requirements-dev.txt')]}
    transitive = {normalized(name): version for name, version in pins('constraints.txt').items()}
    assert transitive and not direct & set(transitive)
    pinned = {normalized(name): version for name, version in
              [*pins('requirements.txt').items(), *pins('requirements-dev.txt').items(), *pins('constraints.txt').items()]}
    installed = {normalized(dist.metadata['Name']): dist.version for dist in metadata.distributions()}
    installed = {name: version for name, version in installed.items() if name not in ('pip', 'setuptools', 'wheel')}
    assert installed == pinned
