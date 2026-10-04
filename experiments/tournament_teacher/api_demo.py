"""Exercise real ASGI teacher API: disabled, recommendation, and abstention."""
import json
import sys

from .api_harness import offline_client,request_body,request_path
from .scenarios import CASES,make_request


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    context = make_request(next(c for c in CASES if c[0]=="1H-1NT-REBID-2NT"))
    context.pop("proposed_call")
    context["task"] = "recommend"
    output = []
    with offline_client() as client:
        response = client.post(request_path(context),json=request_body(context))
        assert response.status_code == 404
        output.append(dict(case="disabled_by_default",http=response.status_code,response=response.json()))
    with offline_client(enabled=True) as client:
        for name in ("recommend","missing_points_abstain"):
            if name != "recommend":
                del context["school_points"]
            payload = request_body(context)
            response = client.post(request_path(context),json=payload)
            assert response.status_code == 200
            answer = response.json()
            assert answer["status"] == ("RECOMMEND" if name=="recommend" else "ABSTAIN")
            output.append(dict(case=name,path=request_path(context),request=payload,
                               http=response.status_code,response=answer))
    print(json.dumps(output,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
