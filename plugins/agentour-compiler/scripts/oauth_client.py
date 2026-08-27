"""OAuth Authorization Code + PKCE client for the Agentour Codex Plugin."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform as host_platform
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler,HTTPServer

from credential_store import delete_credentials,get_credentials,set_credentials

CLIENT_ID="agentour-codex-plugin"
BASE_SCOPES=("openid","profile","offline_access","agent:read","agent:write",
             "repository:read","repository:write","drive:file:read","drive:file:write")


class OAuthClientError(RuntimeError):
    pass


def _json_request(url: str,*,data: bytes|None=None,headers: dict|None=None)->dict:
    request=urllib.request.Request(url,data=data,headers={"Accept":"application/json",**(headers or {})},
                                   method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request,timeout=120) as response:
            content_type=str(response.headers.get("Content-Type") or "").split(";",1)[0].lower()
            if content_type!="application/json":raise OAuthClientError("OAUTH_RESPONSE_CONTENT_TYPE_INVALID")
            value=json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:payload=json.loads(exc.read().decode("utf-8","replace"))
        except (ValueError,UnicodeDecodeError):payload={}
        raise OAuthClientError(str(payload.get("error_code") or payload.get("error") or
                                   f"OAUTH_HTTP_{exc.code}")) from exc
    except (urllib.error.URLError,TimeoutError) as exc:
        raise OAuthClientError("OAUTH_TRANSPORT_UNAVAILABLE") from exc
    if not isinstance(value,dict):raise OAuthClientError("OAUTH_RESPONSE_INVALID")
    return value


def _verify_identity(base_url: str,access_token: str,credentials: dict,
                     expected_scopes: set[str]|None=None)->dict:
    public=_json_request(base_url+"/v1/auth/oidc-config")
    identity=_json_request(base_url+"/v1/plugin/identity",
                           headers={"Authorization":f"Bearer {access_token}"})
    expected_scopes=expected_scopes or set(BASE_SCOPES)
    if (identity.get("issuer")!=public.get("issuer") or identity.get("audience")!=CLIENT_ID or
            not str(identity.get("subject") or "") or
            not expected_scopes.issubset(set(identity.get("scopes") or [])) or
            float(identity.get("expires_at") or 0)<=time.time()):
        raise OAuthClientError("OAUTH_IDENTITY_INVALID")
    if credentials and (credentials.get("issuer")!=identity["issuer"] or
                        credentials.get("subject")!=identity["subject"]):
        raise OAuthClientError("OAUTH_IDENTITY_CHANGED")
    return identity


def _bundle(tokens: dict,identity: dict)->dict:
    bundle={"credential_type":"oauth_public_client_v1","client_id":CLIENT_ID,
        "access_token":str(tokens["access_token"]),"refresh_token":str(tokens["refresh_token"]),
        "expires_at":float(identity["expires_at"]),"issuer":str(identity["issuer"]),
        "subject":str(identity["subject"]),"scopes":list(identity["scopes"]),
        "updated_at":time.time()}
    for key in ("display_name","user_id"):
        if identity.get(key):bundle[key]=str(identity[key])
    return bundle


def _exchange(base_url: str,payload: dict)->dict:
    return _json_request(base_url+"/v1/oauth/token",
        data=urllib.parse.urlencode(payload).encode(),
        headers={"Content-Type":"application/x-www-form-urlencoded"})


def refresh(platform: str,base_url: str,credentials: dict)->str:
    try:
        tokens=_exchange(base_url,{"grant_type":"refresh_token","client_id":CLIENT_ID,
                                   "refresh_token":credentials["refresh_token"]})
        expected=set(credentials.get("scopes") or BASE_SCOPES)
        identity=_verify_identity(base_url,str(tokens["access_token"]),credentials,expected)
        set_credentials(platform,_bundle(tokens,identity))
        return str(tokens["access_token"])
    except (KeyError,OAuthClientError):
        delete_credentials(platform)
        raise OAuthClientError("OAUTH_REAUTHORIZATION_REQUIRED")


def switch_account(platform: str,base_url: str)->dict:
    credentials=get_credentials(platform)
    if credentials.get("credential_type")=="tenant_access_token_v1":
        raise OAuthClientError("TENANT_ACCOUNT_SWITCH_UNSUPPORTED")
    refresh_token=str(credentials.get("refresh_token") or "")
    if refresh_token:
        _json_request(base_url+"/v1/oauth/revoke",
            data=json.dumps({"token":refresh_token},separators=(",",":")).encode(),
            headers={"Content-Type":"application/json"})
    delete_credentials(platform)
    return login(platform,base_url)


def login(platform: str,base_url: str,*,timeout: float=300,
          required_scopes: tuple[str,...]=())->dict:
    requested_scopes=tuple(dict.fromkeys((*BASE_SCOPES,*required_scopes)))
    state=secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(32)
    verifier=secrets.token_urlsafe(64)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    result:dict[str,str]={};ready=threading.Event()

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed=urllib.parse.urlsplit(self.path)
            if parsed.path!="/oauth/callback":self.send_error(404);return
            values=urllib.parse.parse_qs(parsed.query,strict_parsing=True,max_num_fields=8)
            result.update({key:items[0] for key,items in values.items() if len(items)==1})
            body="<!doctype html><meta charset='utf-8'><title>Agentour 授权完成</title><style>body{font:18px system-ui;padding:12vh;text-align:center;background:#f5f7fb;color:#182033}</style><h1>授权已完成</h1><p>可以关闭此页面并返回 Codex。</p>".encode("utf-8")
            self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(body)))
            self.end_headers();self.wfile.write(body);ready.set()
        def log_message(self,*_args):return

    server=HTTPServer(("127.0.0.1",0),Callback);server.timeout=min(timeout,1)
    callback=f"http://127.0.0.1:{server.server_port}/oauth/callback"
    query=urllib.parse.urlencode({"response_type":"code","client_id":CLIENT_ID,
        "redirect_uri":callback,"scope":" ".join(requested_scopes),"code_challenge":challenge,
        "code_challenge_method":"S256","state":state,"nonce":nonce,
        "device_name":host_platform.node() or "Codex Plugin"})
    authorization_url=base_url+"/v1/oauth/authorize?"+query
    if not webbrowser.open(authorization_url,new=1):
        print(json.dumps({"authorization_required":True,"authorization_url":authorization_url},
                         ensure_ascii=False),flush=True)
    deadline=time.monotonic()+timeout
    try:
        while not ready.is_set() and time.monotonic()<deadline:server.handle_request()
    finally:server.server_close()
    if not ready.is_set():raise OAuthClientError("OAUTH_CALLBACK_TIMEOUT")
    if result.get("state")!=state:raise OAuthClientError("OAUTH_STATE_INVALID")
    if result.get("error"):raise OAuthClientError("OAUTH_ACCESS_DENIED")
    code=str(result.get("code") or "")
    if not code:raise OAuthClientError("OAUTH_CODE_MISSING")
    tokens=_exchange(base_url,{"grant_type":"authorization_code","client_id":CLIENT_ID,
        "code":code,"redirect_uri":callback,"code_verifier":verifier})
    if tokens.get("nonce")!=nonce:raise OAuthClientError("OAUTH_NONCE_INVALID")
    previous=get_credentials(platform)
    identity=_verify_identity(base_url,str(tokens.get("access_token") or ""),previous,
                              set(requested_scopes))
    credentials=_bundle(tokens,identity);set_credentials(platform,credentials)
    return {key:value for key,value in identity.items() if key not in {"access_token","refresh_token"}}


def access_token(platform: str,base_url: str,*,interactive: bool=False,
                 required_scopes: tuple[str,...]=())->str:
    credentials=get_credentials(platform)
    if credentials.get("credential_type")=="tenant_access_token_v1":
        granted=set(credentials.get("scopes") or [])
        if not required_scopes or set(required_scopes).issubset(granted):
            if float(credentials.get("expires_at") or 0)>time.time()+30:
                return str(credentials["access_token"])
            raise OAuthClientError("TENANT_ACCESS_TOKEN_EXPIRED")
        raise OAuthClientError("TENANT_ACCESS_SCOPE_REQUIRED")
    required=set(BASE_SCOPES).union(required_scopes)
    granted=set(credentials.get("scopes") or []) if credentials else set()
    if credentials and required.issubset(granted) and \
            float(credentials.get("expires_at") or 0)>time.time()+60:
        return str(credentials["access_token"])
    if credentials and required.issubset(granted):
        try:return refresh(platform,base_url,credentials)
        except OAuthClientError:
            if not interactive:raise
    if not interactive:raise OAuthClientError("OAUTH_AUTHORIZATION_REQUIRED")
    login(platform,base_url,required_scopes=required_scopes)
    credentials=get_credentials(platform)
    if not credentials or not required.issubset(set(credentials.get("scopes") or [])):
        raise OAuthClientError("OAUTH_CREDENTIAL_STORAGE_FAILED")
    return str(credentials["access_token"])
