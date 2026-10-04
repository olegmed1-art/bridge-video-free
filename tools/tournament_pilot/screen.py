"""Local, on-demand screen. Live token stays in this process, never the browser."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from urllib.request import Request, build_opener, HTTPRedirectHandler
from uuid import UUID

from bridge_school_api import tournament_teacher as teacher
from .package import catalog, position, envelope, formal_package

UPSTREAM = "https://bridge-video-free.vercel.app"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def public_answer(result, call):
    if (not isinstance(result,dict) or result.get("status") not in {"SUPPORTED","CONTRADICTED","ABSTAIN"}
            or "action" not in result or result["action"] is not None
            or any(result.get(k) is not False for k in ("persisted","queued","finalized"))
            or result.get("teacher_version")!=teacher.VERSION or result.get("teacher_system")!=teacher.PROFILE
            or result.get("scope_key")!=teacher.SCOPE or result.get("assessment_scope")!="shape_meaning_only"
            or result.get("proposed_call")!=call or not isinstance(result.get("source_bindings"),list)):
        raise ValueError("Unexpected answer contract")
    bindings=result["source_bindings"]
    if result["status"]=="ABSTAIN":
        if bindings:
            raise ValueError("Abstention has active bindings")
    else:
        entry=next(e for e in formal_package()["rules"] if e["payload"]["source_rule"]["call"]==call)
        source=entry["payload"]["source_rule"]
        if (len(bindings)!=1 or bindings[0].get("rule_key")!=entry["rule_key"]
                or bindings[0].get("payload_sha256")!=entry["payload_sha256"]
                or any(bindings[0].get(k)!=source[k] for k in ("source_url","decision_ids","original_excerpt","teacher_excerpt"))
                or result.get("observed_shape")!={"S":3,"H":1,"D":4,"C":5}
                or result["status"]!=("SUPPORTED" if call=="3H" else "CONTRADICTED")):
            raise ValueError("Answer does not bind the reviewed canary meaning")
    return {"status":result["status"],"reason":result.get("reason"),
        "explanation":result.get("explanation"),"sources":[
            {k:b[k] for k in ("rule_key","decision_ids","original_excerpt","teacher_excerpt")}
            for b in result.get("source_bindings",[])]}


def live_sender(position_id, token):
    position_id = str(UUID(position_id))
    if not token:
        raise ValueError("Existing BRIDGE_API_TOKEN is required; no new key is created")
    opener = build_opener(NoRedirect())
    def send(call):
        request=Request(UPSTREAM+f"/v1/ai/positions/{position_id}/teacher-evidence",
            data=json.dumps(envelope(call)).encode(),headers={"Content-Type":"application/json","Authorization":"Bearer "+token})
        with opener.open(request,timeout=15) as response:
            result=json.loads(response.read(65536))
            if result.get("position_id")!=position_id or result.get("teacher_key")!=teacher.KEY:
                raise ValueError("Unexpected live position or teacher")
            return result
    return send


def create_server(*, port=8765, sender=None, revoked=False):
    demo = sender is None
    if demo:
        sender=lambda call:teacher.evaluate(position(UUID(int=101)),call,[] if revoked else [catalog(call)])
    nonce=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data, kind="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type",kind)
            self.send_header("Content-Length",str(len(data)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("X-Frame-Options","DENY")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("Content-Security-Policy",f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def host_ok(self):
            return self.headers.get("Host")==f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.host_ok():
                return self.reply(403,b'{}')
            if self.path != "/":
                return self.reply(404,b'{}')
            page=Path(__file__).with_name("screen.html").read_text(encoding="utf-8")
            mode="Локальная учебная проверка · без production" if demo else "Ограниченный пилот · существующий API"
            self.reply(200,page.replace("__NONCE__",nonce).replace("__MODE__",mode).encode(),"text/html; charset=utf-8")

        def do_POST(self):
            origin=f"http://127.0.0.1:{self.server.server_port}"
            if (not self.host_ok() or self.headers.get("Origin")!=origin
                    or not secrets.compare_digest(self.headers.get("X-Pilot-Nonce","").encode(),nonce.encode())
                    or self.headers.get("Content-Type")!="application/json"):
                return self.reply(403,b'{}')
            if self.path != "/assess":
                return self.reply(404,b'{}')
            try:
                length=int(self.headers.get("Content-Length","0"))
                if not 0 < length <= 64:
                    raise ValueError()
                request=json.loads(self.rfile.read(length))
                if not isinstance(request,dict) or set(request)!={"call"} or request["call"] not in ("3H","3S"):
                    raise ValueError()
            except (ValueError,TypeError):
                return self.reply(422,b'{}')
            try:
                answer=public_answer(sender(request["call"]),request["call"])
            except Exception:
                # Never return credential-bearing upstream exceptions or bodies.
                return self.reply(503,json.dumps({"error":"Не удалось проверить доступ к API. Результат не получен."},ensure_ascii=False).encode())
            self.reply(200,json.dumps(answer,ensure_ascii=False).encode())
    return ThreadingHTTPServer(("127.0.0.1",port),Handler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-position",help="Existing approved synthetic position UUID; otherwise offline demonstration")
    parser.add_argument("--revoked",action="store_true",help="Offline demonstration of absent/revoked binding")
    parser.add_argument("--port",type=int,default=8765)
    args=parser.parse_args()
    if args.live_position and args.revoked:
        parser.error("--revoked is offline only")
    sender=live_sender(args.live_position,os.environ.get("BRIDGE_API_TOKEN","")) if args.live_position else None
    with create_server(port=args.port,sender=sender,revoked=args.revoked) as server:
        print(f"Open http://127.0.0.1:{server.server_port} — {'LIVE PILOT' if sender else 'OFFLINE DEMO'}",flush=True)
        server.serve_forever()


if __name__=="__main__":
    main()
