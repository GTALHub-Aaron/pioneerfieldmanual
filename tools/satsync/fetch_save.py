#!/usr/bin/env python3
"""Download the newest save of the running session from a Satisfactory dedicated server.

Reads SF_HOST (host:port, e.g. 1.2.3.4:7777) and either SF_TOKEN (from `server.GenerateAPIToken`)
or SF_ADMIN_PASSWORD (the server's admin password, used to log in each run) from the environment and writes the save to the path given as the first argument.
Nothing secret is ever printed.
"""
import json, os, ssl, sys, urllib.request

def call(host, token, function, data=None, raw=False):
    body = json.dumps({'function': function, 'data': data or {}}).encode()
    headers = {'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(f'https://{host}/api/v1', data=body, method='POST', headers=headers)
    # dedicated servers use a self-signed certificate
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx, timeout=120) as r:
        payload = r.read()
    if raw:
        return payload
    return json.loads(payload or b'{}').get('data', {})

def g(o, k, default=None):
    """Read a field whether the server spells it camelCase or PascalCase."""
    for key in (k, k[0].upper()+k[1:]):
        if isinstance(o, dict) and key in o: return o[key]
    return default

def main(out):
    host, token = os.environ.get('SF_HOST', '').strip(), os.environ.get('SF_TOKEN', '').strip()
    pw = os.environ.get('SF_ADMIN_PASSWORD', '')
    if not host or not (token or pw):
        sys.exit('SF_HOST and either SF_TOKEN or SF_ADMIN_PASSWORD must be set')
    if not token:
        d = call(host, None, 'PasswordLogin', {'MinimumPrivilegeLevel': 'Administrator', 'Password': pw})
        token = g(d, 'authenticationToken')
        if not token:
            sys.exit('admin password login failed (check SF_ADMIN_PASSWORD)')
    d = call(host, token, 'EnumerateSessions')
    sessions = g(d, 'sessions') or []
    if not sessions:
        sys.exit('server reports no sessions')
    cur = g(d, 'currentSessionIndex', 0)
    s = sessions[cur if 0 <= cur < len(sessions) else 0]
    saves = sorted(g(s, 'saveHeaders') or [], key=lambda h: str(g(h, 'saveDateTime', '')), reverse=True)
    if not saves:
        sys.exit('no saves in session')
    name = g(saves[0], 'saveName')
    data = call(host, token, 'DownloadSaveGame', {'SaveName': name}, raw=True)
    if data[:1] == b'{':
        sys.exit('download refused: ' + str(g(json.loads(data), 'errorCode', 'unknown')))
    open(out, 'wb').write(data)
    print(f'session {g(s, "sessionName")}: downloaded {name} ({len(data)//1024} KB)')

if __name__ == '__main__':
    main(sys.argv[1])
