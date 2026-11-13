import json
import subprocess
import sys
import os
import unittest
from pathlib import Path
from cx.platform import Platform, Fault, video_enc
from cx import parser

COURSE={'id':'11','recruit_id':'22','name':'合成课程'}
RAW={'courseId':'11','clazzId':'22','cpi':'33','title':'合成课程'}
POINT={'id':'44','title':'合成章节','locked':False,'finished':False}
WORK='<form action="/mooc-ans/work/addStudentWorkNew"><input name="workId" value="55"><input name="workAnswerId" value="66"><input name="answerq1" value="B"><div class="singleQuesId" data="q1"><div class="TiMu" data="0"><div class="Zy_TItle">1.（2.0分）1+1=?</div><ul><li aria-label="A. 1"></li><li aria-label="B. 2 选择"></li></ul></div></div></form>'

class Host:
    def __init__(self):
        self.rows={}; self.time=100; self.requests=[]; self.responses=[]
    def call(self,method,p):
        if method=='cw.clock.now': return {'now':self.time}
        if method=='cw.session.identity': return {'user_id':'7','organization_id':'8','display_name':'合成账号'}
        if method=='cw.state':
            key=(p['session'],p['key'])
            if 'value' in p: self.rows[key]=json.loads(json.dumps(p['value'])); return {}
            return {'value':json.loads(json.dumps(self.rows.get(key)))}
        if method=='cw.net.fetch':
            self.requests.append(p)
            if not self.responses: raise AssertionError('离线测试不允许未声明的网络请求')
            value=self.responses.pop(0)
            if isinstance(value,Exception): raise value
            return {'status':200,'headers':{},'body':json.dumps(value) if isinstance(value,(dict,list)) else value}
        raise AssertionError('未声明的宿主调用：'+method)

class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.host=Host(); self.p=Platform(self.host,{'source':'live'})
        self.p.state('s','courses',[RAW])
    def job(self,kind='video'):
        value={'_jobid':'j1','_kind':kind,'_point':POINT,'_card':0,'_defaults':{'knowledgeid':'44'},'property':{'objectid':'o1'}}
        self.p.state('s','job:11/22:j1',value)
        return value
    def test_course_folders_and_closed_cards(self):
        text='<ul class="file-list"><li fileid="9"></li></ul><div class="course"><a href="/a?cpi=33"></a><input class="clazzId" value="22"><input class="courseId" value="11"><span class="course-name" title="合成课程"></span></div>'
        self.assertEqual(parser.folders(text),['9']); self.assertEqual(parser.courses(text),[RAW])
        self.assertEqual(parser.courses(text.replace('<a href','<a class="not-open-tip" href')),[])
    def test_card_json_preserves_spaces_and_finds_nested_type_without_mid(self):
        raw={'defaults':{'knowledgeid':'44'},'attachments':[{'jobid':'j1','property':{'type':'video','name':'A B','objectid':'o1'}},{'jobid':'j2','type':'audio','isPassed':True,'property':{'objectid':'o2'}}]}
        values,_,_=parser.cards('mArg = '+json.dumps(raw)+';')
        self.assertEqual(values[0]['property']['name'],'A B'); self.assertEqual(values[0]['_kind'],'video'); self.assertTrue(values[1]['_passed'])
    def test_paper_preserves_existing_answers_and_clean_accessible_options(self):
        value=parser.work(WORK,{'id':'work-55','name':'合成测验'})
        q=value['attempt']['questions'][0]
        self.assertEqual(q['text'],'1+1=?'); self.assertEqual(q['answer'],['B']); self.assertEqual(q['options'][1]['text'],'2')
        self.assertEqual(value['form']['workAnswerId'],'66')
    def test_encrypted_font_without_font_data_stops(self):
        with self.assertRaises(ValueError): parser.work('<style id="cxSecretStyle">bad</style>'+WORK,{'id':'w','name':'w'})
    def test_unknown_open_never_reopens(self):
        self.p.state('s','paper:11/22:work-55',{'opening':True})
        with self.assertRaises(Fault): self.p.dispatch('exam.open',{'session':'s','course':COURSE,'paper_id':'work-55'})
        self.assertEqual(self.host.requests,[])
    def test_submit_intent_prevents_retry_after_unknown_result(self):
        value=parser.work(WORK,{'id':'work-55','name':'合成测验'}); value.update(job='work-55',paper_key='paper:11/22:work-55')
        self.p.state('s','attempt:11/22:66',value)
        self.host.responses=[Fault('platform_unavailable','合成网络故障')]
        params={'session':'s','course':COURSE,'attempt_id':'66','phase':'submit','answers':{'q1':['B']}}
        with self.assertRaises(Fault): self.p.dispatch('exam.write',params)
        with self.assertRaises(Fault): self.p.dispatch('exam.write',params)
        self.assertEqual(len(self.host.requests),1); self.assertEqual(self.host.requests[0]['stage'],'submit')
    def test_readback_requires_server_job_confirmation(self):
        value=parser.work(WORK,{'id':'work-55','name':'合成测验'}); value.update(job='work-55')
        self.p.state('s','attempt:11/22:66',value)
        self.host.responses=[{'data':[]},{'data':[{'jobid':'work-55'}]}]
        params={'session':'s','course':COURSE,'attempt_id':'66'}
        self.assertFalse(self.p.dispatch('exam.readback',params)['confirmed']); self.assertTrue(self.p.dispatch('exam.readback',params)['confirmed'])
    def test_video_signature_and_clip_window(self):
        import hashlib
        expected=hashlib.md5(b'[22][7][j1][o1][12000][d_yHJ!$pdA~5][100000][10_20]').hexdigest()
        self.assertEqual(video_enc('22','7','j1','o1',12,100,'10_20'),expected)
    def test_video_resume_does_not_count_pause_time_and_needs_server_confirmation(self):
        self.job(); info={'duration':100,'begin':10,'end':20,'clip':'10_20','reportUrl':'https://mooc1.chaoxing.com/log','objectid':'o1','jobid':'j1','interval':2,'position':10,'fastforward':False}
        cp={'execution':'old','last':0,'position':99,'confirmed':10,'info':info}
        self.p.video_info=lambda *a:dict(info)
        self.host.responses=[{'isPassed':False}]
        result=self.p.study({'session':'s','course':COURSE,'unit':{'id':'j1'},'checkpoint':cp,'options':{'execution_id':'new'}})
        self.assertEqual(result['checkpoint']['position'],10); self.assertEqual(self.host.requests[0]['body']['isdrag'],'3')
        self.host.time+=10; self.host.responses=[{'isPassed':False}]
        result=self.p.study({'session':'s','course':COURSE,'unit':{'id':'j1'},'checkpoint':result['checkpoint'],'options':{'execution_id':'new'}})
        self.assertEqual(result['status'],'in_progress')
        self.host.time+=15; self.host.responses=[{'isPassed':True}]
        result=self.p.study({'session':'s','course':COURSE,'unit':{'id':'j1'},'checkpoint':result['checkpoint'],'options':{'execution_id':'new'}})
        self.assertEqual(result['status'],'completed')
    def test_seekable_video_sends_start_drag_and_completion_with_full_other_info(self):
        self.job(); self.p.settings['finish_seekable']=True
        info={'duration':30,'begin':10,'end':20,'clip':'10_20','reportUrl':'https://mooc1.chaoxing.com/log','objectid':'o1','jobid':'j1','interval':60,'position':10,'fastforward':True,'otherInfo':'nodeId_44-&courseId=11'}
        self.p.video_info=lambda *a:dict(info)
        self.host.responses=[{'isPassed':False},{'isPassed':False},{'isPassed':True}]
        cp=None
        for _ in range(3):
            result=self.p.study({'session':'s','course':COURSE,'unit':{'id':'j1'},'checkpoint':cp,'options':{'execution_id':'x'}})
            cp=result['checkpoint']; self.host.time+=1
        self.assertEqual(result['status'],'completed')
        self.assertEqual([r['body']['isdrag'] for r in self.host.requests],['3','1','4'])
        self.assertEqual(self.host.requests[1]['body']['playingTime'],'11-20')
        self.assertEqual(self.host.requests[2]['body']['otherInfo'],info['otherInfo'])
    def test_empty_chapter_requires_official_directory_readback(self):
        self.job('empty')
        self.host.responses=['','<div id="cur44"><a class="clicktitle">合成章节</a><span class="bntHoverTips">已完成</span></div>']
        result=self.p.study({'session':'s','course':COURSE,'unit':{'id':'j1'},'options':{'execution_id':'x'}})
        self.assertEqual(result['status'],'completed'); self.assertEqual(len(self.host.requests),2)
    def test_live_does_not_guess_duration_or_complete_from_elapsed_time(self):
        value=self.job('live'); value['property'].update(liveId='l1',streamName='stream',vdoid='v1')
        self.p.state('s','job:11/22:j1',value)
        self.host.responses=[{'temp':{'data':{'duration':60}}},'@success',{'temp':{'data':{'duration':60}}},'@success',{'temp':{'data':{'duration':60,'isPassed':True}}}]
        p={'session':'s','course':COURSE,'unit':{'id':'j1'},'options':{'execution_id':'x'}}
        first=self.p.study(p); self.host.time+=60
        second=self.p.study({**p,'checkpoint':first['checkpoint']}); self.assertEqual(second['status'],'in_progress')
        self.assertEqual(self.p.study({**p,'checkpoint':second['checkpoint']})['status'],'completed')
        self.host.responses=[{'temp':{'data':{}}}]
        self.assertEqual(self.p.study(p)['status'],'needs_human')
        self.assertEqual([r['stage'] for r in self.host.requests],['read','study','read','study','read','read'])
    def test_font_decoder_maps_synthetic_glyph_and_rejects_unmapped_private_character(self):
        from fontTools.fontBuilder import FontBuilder
        from fontTools.pens.ttGlyphPen import TTGlyphPen
        from cx.font import decoder, glyph_hash
        from unittest.mock import patch
        from io import BytesIO
        import base64
        font=FontBuilder(1000,isTTF=True); font.setupGlyphOrder(['.notdef','uniE001'])
        pen=TTGlyphPen(None); pen.moveTo((0,0)); pen.lineTo((100,0)); pen.lineTo((100,100)); pen.closePath(); glyph=pen.glyph()
        font.setupCharacterMap({0xe001:'uniE001'}); font.setupGlyf({'.notdef':TTGlyphPen(None).glyph(),'uniE001':glyph})
        font.setupHorizontalMetrics({name:(500,0) for name in ['.notdef','uniE001']}); font.setupHorizontalHeader(ascent=800,descent=-200)
        font.setupNameTable({'familyName':'Synthetic','styleName':'Regular'}); font.setupOS2(); font.setupPost(); font.setupMaxp()
        data=BytesIO(); font.save(data)
        text='<style id="cxSecretStyle">src:url(data:font/ttf;base64,'+base64.b64encode(data.getvalue()).decode()+')</style>'
        with patch('cx.font.json.loads',return_value={'uni4E00':glyph_hash(glyph)}): decode=decoder(text)
        self.assertEqual(decode('\ue001'),'一')
        with self.assertRaises(ValueError): decode('\ue002')
    def test_failed_mobile_video_object_falls_back_without_treating_milliseconds_as_seconds(self):
        job=self.job(); job.update(mid='m',playTime=12000,otherinfo='nodeId_44-')
        self.host.responses=[{'msg':'接口未开放'},{'status':'success','dtoken':'synthetic','duration':100}]
        info=self.p.video_info('s',RAW,job)
        self.assertEqual(info['position'],12); self.assertEqual(info['otherInfo'],'nodeId_44-')
    def test_mobile_http_report_url_uses_https_before_study(self):
        job=self.job(); job['mid']='m'
        self.host.responses=[{'data':{'videoObject':{'duration':100,'jobid':'j1','objectid':'o1','reportUrl':'http://mooc1-api.chaoxing.com/mooc-ans/multimedia/log/a/3/token','playTime':12}}}]
        info=self.p.video_info('s',RAW,job)
        self.assertEqual(info['reportUrl'],'https://mooc1-api.chaoxing.com/mooc-ans/multimedia/log/a/3/token')
        self.assertEqual(info['position'],12)
    def test_untrusted_http_report_url_is_rejected(self):
        job=self.job(); job['mid']='m'
        self.host.responses=[{'data':{'videoObject':{'duration':100,'jobid':'j1','objectid':'o1','reportUrl':'http://example.net/log'}}}]
        with self.assertRaisesRegex(Fault,'视频上报地址不安全'):
            self.p.video_info('s',RAW,job)
    def test_document_empty_response_uses_readback(self):
        self.job('document'); raw={'attachments':[{'jobid':'j1','type':'document','isPassed':True,'property':{}}]}
        self.host.responses=['','mArg='+json.dumps(raw)+';']
        result=self.p.study({'session':'s','course':COURSE,'unit':{'id':'j1'},'options':{'execution_id':'x'}})
        self.assertEqual(result['status'],'completed'); self.assertEqual(self.host.requests[0]['stage'],'study')
    def test_work_card_pass_flag_alone_is_not_submitted(self):
        self.p.state('s','outline:11/22:papers',{'points':[POINT],'units':[],'papers':[],'finished':[],'unfinished':[],'nodes':{},'seen':{},'complete':False})
        self.host.responses=['mArg='+json.dumps({'attachments':[{'jobid':'work-55','type':'workid','isPassed':True,'property':{'title':'合成作业'}}]})+';']
        result=self.p.outline({'session':'s','course':COURSE,'cursor':{'phase':'cards','point':0,'card':0}},'papers')
        self.assertFalse(result['papers'][0]['done']); self.assertTrue(result['papers'][0]['available'])
    def test_fixture_never_calls_network(self):
        p=Platform(self.host,{'source':'fixture'})
        self.assertEqual(p.dispatch('catalog.courses',{'session':'fixture'})['courses'][0]['id'],'synthetic-cx-course')
        self.assertEqual(p.dispatch('auth.complete',{'session':'fixture'})['account_raw'],'synthetic-cx-user')
        self.assertEqual(self.host.requests,[])
    def test_fixture_password_waits_for_submit_before_connecting(self):
        p=Platform(self.host,{'source':'fixture'})
        self.assertEqual(p.dispatch('auth.begin',{'session':'fixture','method':'password'})['challenge']['kind'],'password')
        self.assertEqual(p.dispatch('auth.poll',{'session':'fixture'})['state'],'waiting')
        p.dispatch('auth.submit',{'session':'fixture','fields':{'username':'synthetic','password':'synthetic'}})
        self.assertEqual(p.dispatch('auth.poll',{'session':'fixture'})['state'],'connected')
        self.assertEqual(self.host.requests,[])
    def test_rpc_subprocess_responds_to_describe_and_exits_at_eof(self):
        entry=Path(__file__).parents[1]/'main.py'
        env={**os.environ,'CF_MODULE_SETTINGS':'{"source":"fixture"}'}
        result=subprocess.run([sys.executable,str(entry)],input=json.dumps({'jsonrpc':'2.0','id':1,'method':'platform.describe','params':{}})+'\n',text=True,capture_output=True,env=env,check=True)
        self.assertEqual(json.loads(result.stdout)['result']['platform']['id'],'cx')

if __name__=='__main__': unittest.main()
