/* Ephemeral test cookie for an existing verified DEV account; never log or persist it. */
const {execFileSync}=require('node:child_process');
const path=require('node:path');
module.exports=function(root){
 return JSON.parse(execFileSync(path.join(root,'.venv/bin/python'),['-B','-c',`
import json,sqlite3
import dev_web
app=dev_web.application
with sqlite3.connect('file:/home/jean/digger-dev/instance/dev/digger.sqlite?mode=ro',uri=True) as c:
 row=c.execute("SELECT id FROM users WHERE is_verified=1 AND role='admin' ORDER BY id LIMIT 1").fetchone()
 if not row: raise RuntimeError('Verified existing DEV account required')
value=app.session_interface.get_signing_serializer(app).dumps({'user_id':row[0],'_permanent':True})
print(json.dumps({'name':app.config['SESSION_COOKIE_NAME'],'value':value,'url':'http://127.0.0.1:17010','httpOnly':True,'sameSite':'Lax'}))
`],{cwd:root,encoding:'utf8'}));
};
