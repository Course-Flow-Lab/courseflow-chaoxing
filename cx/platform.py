import base64
import hashlib
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from . import parser

BASE = 'https://mooc1.chaoxing.com'
HOME = 'https://mooc2-ans.chaoxing.com/mooc2-ans'
MOBILE = 'https://mooc1-api.chaoxing.com'
PASSPORT = 'https://passport2.chaoxing.com'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129.0 Safari/537.36'
MOBILE_UA = 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/129.0 Mobile Safari/537.36'

class Fault(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

def number(value, default=0):
    try: return int(float(value))
    except (ValueError, TypeError): return default

def job_ids(data):
    result = set()
    if isinstance(data, dict):
        ident = data.get('jobid', data.get('jobId'))
        if ident is not None: result.add(str(ident))
        for value in data.values(): result.update(job_ids(value))
    elif isinstance(data, list):
        for value in data: result.update(job_ids(value))
    return result

def video_enc(clazz, uid, job, obj, seconds, duration, clip):
    text = '[%s][%s][%s][%s][%s][d_yHJ!$pdA~5][%s][%s]' % (clazz, uid, job, obj, int(seconds)*1000, int(duration)*1000, clip)
    return hashlib.md5(text.encode()).hexdigest()

class Platform:
    def __init__(self, host, settings):
        self.host, self.settings = host, settings
        self.fixture = settings.get('source', 'live') == 'fixture'
        self.manifest = json.loads((Path(__file__).parent.parent/'courseflow.module.json').read_text())

    def state(self, session, key, value=...):
        params = {'session': session, 'namespace': 'cx-v1', 'key': key}
        if value is not ...: params['value'] = value
        result = self.host.call('cw.state', params)
        return result.get('value') if value is ... else None

    def now(self): return self.host.call('cw.clock.now', {})['now']
    def identity(self, session): return self.host.call('cw.session.identity', {'session': session})

    def request(self, session, url, data=None, method='GET', stage='read', binary=False, mobile=False, optional=False):
        headers = {'User-Agent': MOBILE_UA if mobile else UA, 'Referer': HOME+'/visit/interaction'}
        params = {'session': session, 'url': url, 'method': method, 'headers': headers, 'stage': stage, 'commit': stage in ('open','draft','submit','study'), 'binary': binary,
                  'timeout_ms': 3000 if optional else 10000 if stage == 'read' else 25000}
        if data is not None:
            params.update(encoding='form' if method == 'POST' else 'query', body={k: 'true' if v is True else 'false' if v is False else '' if v is None else str(v) for k,v in data.items()})
        try:
            response = self.host.call('cw.net.fetch', params)
            if response['status'] != 200:
                raise Fault('platform_unavailable', '超星接口暂不可用')
            body = response.get('body', '')
            if not binary and ('antispiderShowVerify' in body or re.search(r'<title>\s*(?:超星)?登录', body)):
                raise Fault('platform_verification_required', '请在超星官方页面完成验证后继续')
            return body
        except Fault:
            if optional: return None
            raise

    def api(self, session, url, data=None, method='GET', stage='read', mobile=False, optional=False):
        body = self.request(session,url,data,method,stage,mobile=mobile,optional=optional)
        if body is None: return None
        try: return json.loads(body)
        except ValueError:
            if optional: return None
            raise Fault('platform_protocol_changed','平台没有返回可识别的 JSON')

    def course_key(self, course): return str(course['id'])+'/'+str(course['recruit_id'])
    def course(self, session, public):
        values = self.state(session,'courses') or []
        found = next((c for c in values if c['courseId']==str(public['id']) and c['clazzId']==str(public['recruit_id'])), None)
        if not found: raise Fault('not_found','课程缓存不存在，请先刷新课程列表')
        return found

    def dispatch(self, method, params):
        if method == 'platform.describe': return {'module': {'name':'courseflow-chaoxing','version':self.manifest['version'],'module_api':1}, 'platform':self.manifest['platform']}
        if self.fixture: return self.fixture_call(method,params)
        if method.startswith('auth.'): return self.auth(method,params)
        if method == 'catalog.courses': return self.courses(params)
        if method in ('catalog.units','catalog.papers'): return self.outline(params, 'papers' if method.endswith('papers') else 'units')
        if method == 'study.step': return self.study(params)
        if method.startswith('exam.'): return self.exam(method,params)
        raise Fault('unsupported_operation','超星模块未提供此能力')

    def auth(self, method, p):
        session = p['session']
        key = 'challenge:'+session
        if method == 'auth.begin':
            if p.get('method') == 'password':
                self.state(session,key,{'kind':'password','connected':False})
                return {'challenge':{'id':session,'kind':'password','expires_at':self.now()+180,'fields':[{'name':'username','label':'学习通账号','type':'text'},{'name':'password','label':'密码','type':'password'}]}}
            text = self.request(session,PASSPORT+'/login',{'fid':'','newversion':'true','refer':'https%3A%2F%2Fi.chaoxing.com'})
            root = parser.tree(text)
            token = {'uuid':parser.field(root,'uuid'),'enc':parser.field(root,'enc'),'kind':'qr'}
            if not token['uuid'] or not token['enc']: raise Fault('platform_protocol_changed','登录页缺少二维码参数')
            self.state(session,key,token)
            image = self.request(session,PASSPORT+'/createqr',{'uuid':token['uuid'],'fid':-1},binary=True)
            return {'challenge':{'id':session,'kind':'qr','image':'data:image/png;base64,'+image,'expires_at':self.now()+180}}
        if method == 'auth.submit':
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import pad
            public_key = b'u2oh6Vu^HWe4_AES'
            encrypt = lambda text: base64.b64encode(AES.new(public_key,AES.MODE_CBC,public_key).encrypt(pad(text.encode(),16))).decode()
            fields = p.get('fields') or {}
            if not fields.get('username') or not fields.get('password'): raise Fault('invalid_params','请填写学习通账号和密码')
            result = self.api(session,PASSPORT+'/fanyalogin',{'fid':-1,'uname':encrypt(fields['username']),'password':encrypt(fields['password']),'refer':'https%3A%2F%2Fi.chaoxing.com','t':'true','forbidotherlogin':0,'validate':'','doubleFactorLogin':0,'independentId':0},'POST')
            if not parser.flag(result.get('status')): return {'state':'failed','message':'登录未成功，请核对账号或在官方页面完成额外验证'}
            self.state(session,key,{'kind':'password','connected':True})
            return {'state':'waiting'}
        if method == 'auth.poll':
            token = self.state(session,key)
            if not token: return {'state':'expired'}
            if token['kind'] == 'password': return {'state':'connected' if token.get('connected') else 'waiting'}
            result = self.api(session,PASSPORT+'/getauthstatus/v2',{**{k:token[k] for k in ('uuid','enc')},'doubleFactorLogin':0,'forbidotherlogin':0},'POST')
            if result.get('containTwoFactorLogin'): raise Fault('platform_verification_required','学习通要求手机二次验证，请在官方页面完成')
            if parser.flag(result.get('status')): return {'state':'connected'}
            return {'state':{'4':'scanned','2':'expired','7':'expired','6':'failed'}.get(str(result.get('type')),'waiting')}
        if method in ('auth.complete','auth.verify','auth.keepalive'):
            if method == 'auth.complete': self.request(session,'https://i.mooc.chaoxing.com/space/index')
            self.request(session,HOME+'/visit/courselistdata',{'courseType':1,'courseFolderId':0,'query':'','superstarClass':0},'POST')
            identity = self.identity(session)
            if not identity.get('user_id'): raise Fault('platform_login_expired','平台没有返回有效账号身份，请重新登录')
            return {'display_name':identity.get('display_name') or '学习通账号','account_raw':identity['user_id'],'valid':True}
        raise Fault('unsupported_operation','不支持的登录操作')

    def courses(self, p):
        session = p['session']
        cursor = p.get('cursor')
        if cursor is None:
            cached = self.state(session,'course_listing')
            if cached and cached.get('session') == session and self.now()-cached.get('updated',0)<30:
                return {'courses':self.public_courses(cached['courses'])}
            folders = ['0']+parser.folders(self.request(session,HOME+'/visit/interaction'))[:99]
            self.state(session,'course_scan',[])
            cursor = {'folders':list(dict.fromkeys(folders)),'index':0}
        folders,index = cursor['folders'],number(cursor['index'])
        text = self.request(session,HOME+'/visit/courselistdata',{'courseType':1,'courseFolderId':folders[index],'query':'','superstarClass':0},'POST')
        values = parser.courses(text)
        existing = self.state(session,'course_scan') or []
        seen = {(c['courseId'],c['clazzId']) for c in existing}
        values = [c for c in values if (c['courseId'],c['clazzId']) not in seen]
        self.state(session,'course_scan',existing+values)
        if index+1 >= len(folders):
            self.state(session,'courses',existing+values)
            self.state(session,'course_listing',{'session':session,'updated':self.now(),'courses':existing+values})
        public = self.public_courses(values)
        return {'courses':public,'next_cursor':{'folders':folders,'index':index+1} if index+1<len(folders) else None}

    @staticmethod
    def public_courses(values):
        return [{'id':str(c['courseId']),'recruit_id':str(c['clazzId']),'name':c['title'],'kind':'chaoxing','family':'chaoxing','kind_label':'超星课程','outline_available':True,'progress':None} for c in values]

    def outline(self,p,field):
        session,public = p['session'],p['course']
        course = self.course(session,public)
        completed_key = 'outline:'+self.course_key(public)
        key = completed_key+':'+field
        cache = self.state(session,key)
        cursor = p.get('cursor')
        if cursor is None:
            cache = self.state(session,completed_key)
            if cache and cache.get('cpi') == course['cpi'] and cache.get('complete') and self.now()-cache.get('updated',0)<60:
                return {field:cache[field]}
            text = self.request(session,HOME+'/mycourse/studentcourse',{'courseid':course['courseId'],'clazzid':course['clazzId'],'cpi':course['cpi'],'ut':'s'})
            cache = {'points':parser.points(text),'units':[],'papers':[],'finished':[],'unfinished':[],'nodes':{},'seen':{},'complete':False,'updated':self.now(),'scan':uuid.uuid4().hex,'cpi':course['cpi']}
            self.state(session,key,cache)
            return {field:[],'next_cursor':{'phase':'nodes','scan':cache['scan']}}
        if not cache or cursor.get('scan') != cache.get('scan'):
            raise Fault('platform_unavailable','目录扫描已更新，请重新刷新当前课程')
        phase = cursor['phase']
        if phase == 'nodes':
            fields = 'id,course.fields(id,knowledge.fields(id,name,finishcount,unfinishcount,clickcount).type(video))'
            data = self.api(session,MOBILE+'/gas/clazz',{'id':course['clazzId'],'fields':fields,'personid':course['cpi'],'view':'json'},mobile=True,optional=True)
            def collect(node):
                if isinstance(node,dict):
                    if node.get('id') is not None and 'unfinishcount' in node: cache['nodes'][str(node['id'])] = number(node['unfinishcount']) == 0
                    for v in node.values(): collect(v)
                elif isinstance(node,list):
                    for v in node: collect(v)
            collect(data)
            next_cursor = {'phase':'finished','page':1}
        elif phase in ('finished','unfinished'):
            page = number(cursor.get('page'),1)
            url = MOBILE+'/api/phone/job'+('finishedList' if phase=='finished' else 'unfinishedList')
            data = self.api(session,url,{'courseid':course['courseId'],'clazzid':course['clazzId'],'knowledgeid':'','jobid':'','page':page,'pageSize':20,'view':'json'},mobile=True,optional=True)
            found = job_ids(data)
            new = found-set(cache[phase]); cache[phase] = sorted(set(cache[phase])|found)
            next_cursor = {'phase':phase,'page':page+1} if len(found)>=20 and new and page<20 else {'phase':'unfinished','page':1} if phase=='finished' else {'phase':'cards','point':0,'card':0}
        else:
            return self.outline_cards(p,field,course,key,completed_key,cache)
        self.state(session,key,cache)
        return {field:[],'next_cursor':{**next_cursor,'scan':cache['scan']}}

    def outline_cards(self,p,field,course,key,completed_key,cache):
        session,public,cursor = p['session'],p['course'],p['cursor']
        index = number(cursor.get('point'))
        active = list(cursor.get('active') or [])
        if 'card' in cursor and index < len(cache['points']):
            active.append([index,number(cursor['card'])]); index += 1
        while len(active)<4 and index<len(cache['points']):
            active.append([index,0]); index+=1
        context = getattr(self.host,'context',None)
        deadline = getattr(context,'deadline',None)
        def read_card(item):
            point_index,card = item
            point = cache['points'][point_index]
            if point['locked']: return None
            if deadline is not None: self.host.context.deadline = deadline
            return self.request(session,BASE+'/mooc-ans/knowledge/cards',{'clazzid':course['clazzId'],'courseid':course['courseId'],'knowledgeid':point['id'],'ut':'s','cpi':course['cpi'],'v':'2025-0424-1038-3','mooc2':1,'num':card})
        # 只并发只读卡片；每批最多四张，写入及分页状态仍由调用线程顺序处理。
        with ThreadPoolExecutor(max_workers=4) as readers:
            pages = list(readers.map(read_card,active))
        units,papers,next_active = [],[],[]
        for (point_index,card),text in zip(active,pages):
            point = cache['points'][point_index]
            if point['locked']:
                units.append(self.unit('locked:'+point['id'],point['title'],'empty',point,False,False,'章节尚未解锁，请先完成先修任务后刷新'))
            else:
                jobs,defaults,signature = parser.cards(text)
                seen = cache['seen'].setdefault(point['id'],[])
                digest = hashlib.sha256(signature.encode()).hexdigest() if signature else ''
                ended = not signature or digest in seen or defaults.get('notOpen')
                if ended:
                    if card==0:
                        ident = 'empty:'+point['id']
                        passed = point['finished'] or cache['nodes'].get(point['id'],False)
                        units.append(self.unit(ident,point['title'],'empty',point,passed,not defaults.get('notOpen'),'章节未开放' if defaults.get('notOpen') else ''))
                        self.state(session,'job:'+self.course_key(public)+':'+ident,{'_kind':'empty','_jobid':ident,'property':{},'_defaults':{'knowledgeid':point['id']},'_point':point,'_card':0})
                else:
                    seen.append(digest)
                    for job in jobs:
                        ident = job['_jobid']; job.update(_point=point,_card=card)
                        self.state(session,'job:'+self.course_key(public)+':'+ident,job)
                        passed = ident in cache['finished'] or ident not in cache['unfinished'] and (job['_passed'] or cache['nodes'].get(point['id'],False))
                        prop = job['property']; kind = job['_kind']
                        name = prop.get('name') or prop.get('title') or ident
                        if kind == 'work':
                            passed = ident in cache['finished'] and ident not in cache['unfinished']
                            papers.append({'id':ident,'name':name,'category':'章节测验','available':not passed,'done':passed,'can_redo':False,'can_review':False,'reason':'已完成' if passed else '提交状态未知时由作答页及回读核对','score':None,'attempts_left':None,'ends_at':None})
                        else:
                            public_kind = 'video' if kind=='live' else kind if kind in ('video','audio','document','reading') else 'empty'
                            item = self.unit(ident,name,public_kind,point,passed,kind!='unsupported','请在官方页面处理' if kind=='unsupported' else '')
                            item.update(duration_seconds=number(prop.get('duration')) or None,watched_seconds=number(job.get('playTime',prop.get('playTime'))) // 1000,video_id=str(prop.get('objectid') or job.get('objectId') or '') or None)
                            if item['watched_seconds'] and not passed: item['state']='in_progress'
                            units.append(item)
                    if card<19: next_active.append([point_index,card+1])
        for group,items in [('units',units),('papers',papers)]:
            old = {item['id'] for item in cache[group]}
            fresh = []
            for item in items:
                if item['id'] not in old:
                    old.add(item['id']); fresh.append(item)
            cache[group].extend(fresh)
            if group == field: result = fresh
        complete = index>=len(cache['points']) and not next_active
        if complete:
            cache.update(complete=True,updated=self.now())
            self.state(session,completed_key,cache)
        self.state(session,key,cache)
        return {field:result,'next_cursor':None if complete else {'phase':'cards','point':index,'active':next_active,'scan':cache.get('scan')}}

    @staticmethod
    def unit(ident,name,kind,point,passed,selectable=True,note=''):
        return {'id':ident,'name':name,'chapter_id':point['id'],'chapter_name':point['title'],'lesson_id':ident,'kind':kind,'state':'completed' if passed else 'not_started','selectable':selectable,'selectable_note':note,'duration_seconds':None,'watched_seconds':0,'video_id':None}

    def job(self,p):
        ident = (p.get('unit') or {}).get('id') or p.get('paper_id')
        value = self.state(p['session'],'job:'+self.course_key(p['course'])+':'+str(ident))
        if not value: raise Fault('not_found','任务点缓存不存在，请先刷新目录')
        return value

    def video_info(self,session,course,job):
        prop,defaults = job['property'],job['_defaults']
        obj = prop.get('objectid') or job.get('objectId') or job.get('objectid')
        blob = None
        def unwrap(data):
            if not isinstance(data,dict): return None
            value = data.get('data') or data.get('msg') or data
            if isinstance(value,str):
                try: value=json.loads(value)
                except ValueError: return None
            value=value.get('videoObject') if isinstance(value,dict) else None
            if isinstance(value,str):
                try: value=json.loads(value)
                except ValueError: return None
            return value if isinstance(value,dict) else None
        if job.get('mid') or prop.get('mid'):
            blob=unwrap(self.api(session,MOBILE+'/knowledge/getknowledgecurvideo',{'courseid':course['courseId'],'clazzid':course['clazzId'],'knowledgeid':job['_point']['id'],'cpi':course['cpi'],'mid':job.get('mid') or prop.get('mid')},'POST',mobile=True,optional=True))
        if not blob and (job.get('aid') or job.get('id')):
            blob=unwrap(self.api(session,MOBILE+'/knowledge/getnextknowledgevideo',{'clazzId':course['clazzId'],'courseId':course['courseId'],'knowledgeId':job['_point']['id'],'cardId':defaults.get('cardid',''),'cpi':course['cpi'],'attachmentId':job.get('aid') or job.get('id'),'isPhone':1},mobile=True,optional=True))
        if not blob:
            identity=self.identity(session)
            data=self.api(session,BASE+'/ananas/status/'+str(obj),{'k':identity.get('organization_id',''),'flag':'normal'})
            if data.get('status')!='success' or not data.get('dtoken'): raise Fault('platform_protocol_changed','视频播放信息不可用')
            blob={**data,'jobid':job['_jobid'],'objectid':obj,'reportUrl':BASE+'/mooc-ans/multimedia/log/a/'+str(course['cpi'])+'/'+str(data['dtoken']),'playTime':number(job.get('playTime',prop.get('playTime'))) // 1000,'fastforward':job.get('fastforward',prop.get('fastforward',False))}
        duration=number(blob.get('duration')); begin=max(0,number(blob.get('vbegin'))); vend=number(blob.get('vend'))
        end=min(duration,vend) if vend>0 else duration
        url=str(blob.get('reportUrl') or '')
        report_url=urlparse(url)
        # The mobile player may return an HTTP report URL although its host supports HTTPS.
        if report_url.scheme == 'http' and report_url.hostname == 'mooc1-api.chaoxing.com' and report_url.port is None:
            url=report_url._replace(scheme='https').geturl()
            report_url=urlparse(url)
        if report_url.scheme != 'https' or not report_url.hostname or not (report_url.hostname == 'chaoxing.com' or report_url.hostname.endswith('.chaoxing.com')):
            raise Fault('platform_protocol_changed','视频上报地址不安全或无效')
        if duration<=0 or end<=begin or not url: raise Fault('platform_protocol_changed','视频时长、截取范围或上报地址无效')
        return {**blob,'duration':duration,'begin':begin,'end':end,'clip':'%s_%s'%(begin,vend if vend>0 else duration),'reportUrl':url,'objectid':blob.get('objectid') or blob.get('objectId') or obj,'jobid':str(blob.get('jobid') or blob.get('jobId') or ''),'interval':max(2,number(blob.get('reportTimeInterval'),60)),'position':max(begin,min(end,number(blob.get('playTime')))),'otherInfo':str(blob.get('otherInfo') or job.get('otherinfo') or job.get('otherInfo') or '')}

    def video_report(self,session,course,job,info,position,kind,event=0,drag_from=None):
        uid=self.identity(session).get('user_id')
        if not uid: raise Fault('platform_login_expired','缺少平台账号身份，请重新连接')
        seconds=int(position); clazz=str(info.get('clazzId') or info.get('clazzid') or course['clazzId'])
        payload={'otherInfo':info.get('otherInfo',job.get('otherinfo','')),'playingTime':'%d-%d'%(int(drag_from),seconds) if drag_from is not None else seconds,'duration':info['duration'],'akid':'null','jobid':info['jobid'],'clipTime':info['clip'],'clazzId':clazz,'objectId':info['objectid'],'userid':uid,'isdrag':event,'enc':video_enc(clazz,uid,info['jobid'],info['objectid'],seconds,info['duration'],info['clip']),'rt':str(float(info.get('rt') or 0)),'dtype':'Audio' if kind=='audio' else 'Video','view':'pc','courseId':course['courseId']}
        for key in ('attDuration','attDurationEnc','videoFaceCaptureEnc'):
            if job.get(key): payload[key]=job[key]
        return self.api(session,info['reportUrl'],payload,stage='study')

    def study(self,p):
        session,course=p['session'],self.course(p['session'],p['course'])
        job=self.job(p); kind=job['_kind']; now=self.now(); cp=dict(p.get('checkpoint') or {})
        execution=(p.get('options') or {}).get('execution_id','manual')
        if cp.get('execution')!=execution:
            cp={'execution':execution,'last':now,'position':cp.get('confirmed',0),'confirmed':cp.get('confirmed',0)}
        if kind in ('video','audio'):
            if 'info' not in cp:
                cp['info']=self.video_info(session,course,job); cp['position']=cp['info']['position']; cp['last_report']=now
            info=cp['info']
            if not info['jobid']: return {'status':'blocked','reason':'非任务点音视频，平台不记录完成','checkpoint':cp}
            if not cp.get('started'):
                result=self.video_report(session,course,job,info,cp['position'],kind,3)
                cp.update(started=True,last=now,last_report=now,confirmed=int(cp['position']))
                if parser.flag(result.get('isPassed')): return self.completed(p,cp)
                return {'status':'in_progress','checkpoint':cp,'wait_ms':1000}
            speed=min(1.5,max(1,float((p.get('options') or {}).get('max_speed',1))))
            if not parser.flag(info.get('fastforward')): speed=1
            cp['position']=min(info['end'],cp['position']+max(0,min(20,now-cp['last']))*speed); cp['last']=now
            if self.settings.get('finish_seekable') and parser.flag(info.get('fastforward')) and cp['position']<info['end']:
                result=self.video_report(session,course,job,info,info['end'],kind,1,drag_from=cp['position'])
                cp.update(position=info['end'],last_report=now,confirmed=info['end'])
                if parser.flag(result.get('isPassed')): return self.completed(p,cp)
                return {'status':'in_progress','checkpoint':cp,'wait_ms':1000}
            if now-cp['last_report']>=info['interval'] or cp['position']>=info['end']:
                seconds=int(cp['position'])
                result=self.video_report(session,course,job,info,seconds,kind,4 if seconds>=info['end'] else 0)
                cp.update(last_report=now,confirmed=seconds)
                if parser.flag(result.get('isPassed')): return self.completed(p,cp)
                if seconds>=info['end']:
                    cp['end_attempts']=cp.get('end_attempts',0)+1
                    if cp['end_attempts']>=3: return {'status':'needs_human','reason':'平台连续未确认完成，请核对官方播放进度','checkpoint':cp}
            return {'status':'in_progress','checkpoint':cp,'progress':{'watched_seconds':int(cp['position'])},'wait_ms':min(15_000,info['interval']*1000)}
        if kind=='live': return self.live(p,course,job,cp,now)
        if kind=='unsupported': return {'status':'needs_human','reason':'任务类型需要在官方页面处理','checkpoint':cp}
        point=job['_point']['id']
        if kind=='empty': url=BASE+'/mooc-ans/mycourse/studentstudyAjax'; payload={'courseId':course['courseId'],'clazzid':course['clazzId'],'chapterId':point,'cpi':course['cpi'],'verificationcode':'','mooc2':1,'microTopicId':0,'editorPreview':0}
        else:
            url=BASE+('/mooc-ans/job/document' if kind=='document' else '/ananas/job/readv2')
            payload={'jobid':job['_jobid'],'knowledgeid':point,'courseid':course['courseId'],'clazzid':course['clazzId'],'jtoken':job.get('jtoken',''),'checkMicroTopic':'true','microTopicId':'undefined','courseEngineInfo':'false'}
        if not cp.get('sent'):
            cp['sent']=True
            text=self.request(session,url,payload,stage='study')
            try: result=json.loads(text)
            except ValueError: result={}
            if parser.flag(result.get('status')) or parser.flag(result.get('isPassed')): return self.completed(p,cp)
        return self.verify_resource(p,course,job,cp)

    def verify_resource(self,p,course,job,cp):
        if job['_kind']=='empty':
            text=self.request(p['session'],HOME+'/mycourse/studentcourse',{'courseid':course['courseId'],'clazzid':course['clazzId'],'cpi':course['cpi'],'ut':'s'})
            if any(point['id']==job['_point']['id'] and point['finished'] for point in parser.points(text)): return self.completed(p,cp)
            return {'status':'needs_human','reason':'平台尚未确认空章节完成，请核对官方目录','checkpoint':cp}
        text=self.request(p['session'],BASE+'/mooc-ans/knowledge/cards',{'clazzid':course['clazzId'],'courseid':course['courseId'],'knowledgeid':job['_point']['id'],'cpi':course['cpi'],'num':job['_card'],'ut':'s','mooc2':1})
        values,_,_=parser.cards(text)
        if any(v['_jobid']==job['_jobid'] and v['_passed'] for v in values): return self.completed(p,cp)
        return {'status':'needs_human','reason':'请求已发出，平台尚未确认完成；请在官方页面核对后继续','checkpoint':cp}

    def live(self,p,course,job,cp,now):
        prop,defaults=job['property'],job['_defaults']; session=p['session']
        identity=self.identity(session); uid=identity.get('user_id') or defaults.get('userid')
        result=self.api(session,BASE+'/ananas/live/liveinfo',{'liveid':prop.get('liveId'),'userid':uid,'clazzid':course['clazzId'],'knowledgeid':job['_point']['id'],'courseid':course['courseId'],'jobid':job['_jobid'],'ut':'s'})
        detail=(result.get('temp') or {}).get('data') or result.get('data') or result
        if parser.flag(detail.get('isPassed')): return self.completed(p,cp)
        duration=number(detail.get('duration'))
        if duration<=0: return {'status':'needs_human','reason':'直播未返回可靠时长，请在官方页面处理','checkpoint':cp}
        cp['position']=number(cp.get('position'))+max(0,min(60,now-cp['last'])); cp['last']=now
        if not cp.get('last_report') or now-cp['last_report']>=59:
            if not prop.get('streamName') or not prop.get('vdoid') or not uid: raise Fault('platform_protocol_changed','直播缺少记录观看所需参数')
            text=self.request(session,'https://zhibo.chaoxing.com/saveTimePc',{'streamName':prop['streamName'],'vdoid':prop['vdoid'],'userId':uid,'isStart':0,'t':now*1000,'courseId':course['courseId']},stage='study')
            if text.strip()!='@success': return {'status':'needs_human','reason':'平台未接受直播观看记录','checkpoint':cp}
            cp['last_report']=now; cp['confirmed']=cp['position']
        if cp['position']>duration+120: return {'status':'needs_human','reason':'观看时长已到，平台尚未确认直播完成','checkpoint':cp}
        return {'status':'in_progress','checkpoint':cp,'progress':{'watched_seconds':cp['position']},'wait_ms':15_000}

    def completed(self,p,cp):
        key='outline:'+self.course_key(p['course']); cache=self.state(p['session'],key)
        if cache: cache['updated']=0; self.state(p['session'],key,cache)
        return {'status':'completed','checkpoint':cp}

    def exam(self,method,p):
        session=p['session']; prefix='attempt:'+self.course_key(p['course'])+':'
        if method=='exam.heartbeat': return {'ok':True}
        if method in ('exam.redo','exam.review'): raise Fault('unsupported_operation','已提交章节测验使用原任务报告查看，不会为查看重新开卷')
        if method=='exam.open':
            key='paper:'+self.course_key(p['course'])+':'+str(p['paper_id']); previous=self.state(session,key)
            if previous:
                if previous.get('attempt'): return {'attempt':previous['attempt'],'ready':True}
                raise Fault('platform_protocol_changed','之前的开卷结果未知，请先在官方页面核对；不会重复开卷')
            job=self.job(p); course=self.course(session,p['course']); defaults=job['_defaults']
            paper={'id':p['paper_id'],'name':job['property'].get('title') or job['property'].get('name') or p['paper_id']}
            self.state(session,key,{'opening':True})
            text=self.request(session,BASE+'/mooc-ans/api/work',{'api':1,'workId':job['_jobid'].removeprefix('work-'),'jobid':job['_jobid'],'originJobId':job['_jobid'],'needRedirect':'true','skipHeader':'true','knowledgeid':job['_point']['id'],'ktoken':defaults.get('ktoken',''),'cpi':course['cpi'],'ut':'s','clazzId':course['clazzId'],'type':'','enc':job.get('enc',''),'mooc2':1,'courseid':course['courseId']},stage='open')
            try: value=parser.work(text,paper)
            except ValueError as error: raise Fault('platform_verification_required',str(error))
            value.update(job=job['_jobid'],paper_key=key,submit_intent=False)
            self.state(session,key,value); self.state(session,prefix+value['attempt']['id'],value)
            return {'attempt':value['attempt'],'ready':True}
        key=prefix+str(p.get('attempt_id')); value=self.state(session,key)
        if not value: raise Fault('not_found','找不到原作答，请从原任务继续')
        if method=='exam.write':
            submit=p.get('phase')=='submit'
            if value.get('submit_intent'):
                if submit: raise Fault('platform_protocol_changed','交卷请求已发出，必须先回读；不会重复发送')
                raise Fault('platform_protocol_changed','交卷后不能再覆盖草稿')
            form=dict(value['form']); answers=p.get('answers') or {}; preserve=set(p.get('preserve_ids') or [])
            for question in value['attempt']['questions']:
                ident=question['id']; layout=value['layout'][ident]
                answer=answers.get(ident,question.get('answer') or [])
                if ident in preserve: continue
                if not valid_answer(question,answer):
                    if submit: raise Fault('invalid_params','提交前需要补齐有效答案')
                    continue
                if layout['kind']=='blank' and len(layout['fields'])>1:
                    for name,item in zip(layout['fields'],answer): form[name]=item
                else: form['answer'+ident]=''.join(sorted(answer) if layout['kind']=='multiple' else answer)
                form['answertype'+ident]=layout['code']
            form['answerwqbid']=','.join(q['id'] for q in value['attempt']['questions'])+','
            form['pyFlag']='' if submit else '1'
            if submit:
                value['submit_intent']=True; self.state(session,key,value)
            result=self.api(session,value['action'],form,'POST','submit' if submit else 'draft')
            if result.get('status') is not True and result.get('success') is not True: raise Fault('platform_rejected','平台未确认作答保存，请核对官方页面')
            value.update(form=form,submitted_ack=submit); self.state(session,key,value); self.state(session,value['paper_key'],value)
            return {'done':True,'saved':not submit,'submitted':submit}
        if method=='exam.readback':
            course=self.course(session,p['course'])
            result=self.api(session,MOBILE+'/api/phone/jobfinishedList',{'courseid':course['courseId'],'clazzid':course['clazzId'],'jobid':value['job'],'page':1,'pageSize':20,'view':'json'},mobile=True,optional=True)
            confirmed=value['job'] in job_ids(result)
            return {'confirmed':confirmed,'attempt_id':value['attempt']['id'],'graded_answers':{},'score':None}
        raise Fault('unsupported_operation','不支持的作答操作')

    def fixture_call(self,method,p):
        course={'id':'synthetic-cx-course','recruit_id':'synthetic-class','name':'离线超星课程','kind':'chaoxing','family':'chaoxing','outline_available':True,'progress':None}
        point={'id':'synthetic-chapter','title':'离线章节'}
        if method=='auth.begin':
            self.state(p['session'],'fixture-login',p.get('method')!='password')
            return {'challenge':{'id':p['session'],'kind':'password','fields':[{'name':'username','label':'离线账号','type':'text'},{'name':'password','label':'离线密码','type':'password'}],'expires_at':self.now()+180}} if p.get('method')=='password' else {'challenge':{'id':p['session'],'kind':'qr','image':'data:image/svg+xml;base64,'+base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><text x="8" y="60">offline cx</text></svg>').decode(),'expires_at':self.now()+180}}
        if method=='auth.submit':
            self.state(p['session'],'fixture-login',True)
            return {'state':'waiting'}
        if method=='auth.poll': return {'state':'connected' if self.state(p['session'],'fixture-login') else 'waiting'}
        if method in ('auth.complete','auth.verify','auth.keepalive'): return {'account_raw':'synthetic-cx-user','display_name':'离线超星账号','valid':True}
        if method=='catalog.courses': return {'courses':[course]}
        if method=='catalog.units': return {'units':[self.unit('synthetic-'+kind,'离线'+name,kind,point,False) for kind,name in [('video','视频'),('audio','音频'),('document','文档'),('reading','阅读'),('empty','空章节')]]}
        if method=='catalog.papers': return {'papers':[{'id':'synthetic-work','name':'离线章节测验','category':'章节测验','available':True,'done':False,'can_redo':False,'can_review':False,'reason':'','score':None,'attempts_left':None,'ends_at':None}]}
        if method=='study.step':
            cp=dict(p.get('checkpoint') or {}); cp['steps']=cp.get('steps',0)+1
            return {'status':'completed' if cp['steps']>=2 else 'in_progress','checkpoint':cp,'progress':{'watched_seconds':cp['steps']},'wait_ms':10}
        if method=='exam.open':
            return {'attempt':{'id':'synthetic-attempt','paper_id':'synthetic-work','name':'离线章节测验','expires_at':None,'questions':[{'id':'synthetic-question','text':'1 + 1 = ?','kind':'single','options':[{'id':'A','text':'1'},{'id':'B','text':'2'}],'answer':[],'images':[],'blanks':0}]},'ready':True}
        if method=='exam.write':
            self.state(p['session'],'fixture-submitted',p.get('phase')=='submit')
            return {'done':True,'saved':p.get('phase')!='submit','submitted':p.get('phase')=='submit'}
        if method=='exam.readback': return {'confirmed':bool(self.state(p['session'],'fixture-submitted')),'attempt_id':'synthetic-attempt','graded_answers':{'synthetic-question':['B']}}
        if method=='exam.heartbeat': return {'ok':True}
        raise Fault('unsupported_operation','离线样例未提供此操作')

def valid_answer(question,answer):
    if not isinstance(answer,list) or not answer or any(not isinstance(a,str) or not a.strip() for a in answer): return False
    kind=question['kind']; ids={o['id'] for o in question['options']}
    if kind=='single': return len(answer)==1 and answer[0] in ids
    if kind=='multiple': return len(set(answer))==len(answer) and all(a in ids for a in answer)
    if kind=='judge': return len(answer)==1 and answer[0] in ('true','false')
    if kind=='blank': return len(answer)==question['blanks']
    return kind=='essay' and len(answer)==1
