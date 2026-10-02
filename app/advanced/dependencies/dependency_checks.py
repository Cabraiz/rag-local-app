"""Offline regression gate: real SDK imports, compatible pins, no model calls."""
import importlib.metadata as metadata
import json
import logging

logging.disable(logging.CRITICAL)


def main():
    import deepagents
    import deepeval
    import judge_lab
    import task_lab
    from free_model import Budget, FreeChatModel, LabBlocked
    from packaging.requirements import Requirement

    checks = []
    def check(name, condition):
        assert condition, name
        checks.append(name)

    check("real_sdk_imports", deepagents is not None and deepeval is not None)
    check("real_adapters_import", judge_lab.FreeJudge is not None
          and task_lab.DeepAgentsTaskAdapter is not None)
    click = metadata.version("click")
    constraints = [Requirement(r) for r in metadata.requires("deepeval") or []
                   if Requirement(r).name.lower() == "click"]
    check("deepeval_click_constraint_satisfied", bool(constraints)
          and all(click in c.specifier for c in constraints))
    check("packaging_dependency_installed", bool(metadata.version("setuptools")))
    budget = Budget(max_calls=0)
    try:
        budget.reserve()
    except LabBlocked as error:
        check("zero_budget_denied_before_configuration", str(error) == "TASK_MODEL_BUDGET"
              and budget.calls == 0)
    else:
        raise AssertionError("MODEL_CALL_ALLOWED")
    def execute(command: str) -> str:
        """Forbidden tool: no implementation or external effects."""
        raise AssertionError("TOOL_EXECUTED")
    try:
        FreeChatModel(Budget()).bind_tools([execute])
    except LabBlocked as error:
        check("forbidden_tool_denied_before_model", str(error) == "TASK_TOOL_SURFACE_NOT_AUTHORIZED")
    else:
        raise AssertionError("FORBIDDEN_TOOL_ALLOWED")
    print(json.dumps({"passed": True, "checks": checks, "model_calls": 0,
                      "versions": {p: metadata.version(p) for p in
                                   ("deepagents", "deepeval", "click", "setuptools")}}))


if __name__ == "__main__":
    main()
