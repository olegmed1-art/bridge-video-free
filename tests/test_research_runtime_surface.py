from assistant_lab.research_api import refresh_research_job, submit_research_job


def test_research_api_helpers_are_callable():
    assert callable(submit_research_job)
    assert callable(refresh_research_job)
