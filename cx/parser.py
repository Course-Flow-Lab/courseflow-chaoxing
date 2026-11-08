import json
import re
from urllib.parse import urljoin, parse_qs, urlparse
from lxml import html
from .font import decoder

BASE = 'https://mooc1.chaoxing.com'
def tree(text):
    return html.document_fromstring(text or '<html></html>')
def cls(node, name, tag='*'):
    return node.xpath('.//%s[contains(concat(" ",normalize-space(@class)," ")," %s ")]' % (tag, name))
def plain(node):
    return ''.join(node.itertext()).strip() if node is not None else ''
def field(root, name):
    values = root.xpath('.//input[@id=$n or @name=$n]/@value', n=name)
    return values[0] if values else ''
def flag(value):
    return value is True or str(value).lower() in ('1', 'true', 'yes')
def courses(text):
    root = tree(text)
    result = []
    for card in cls(root, 'course', 'div'):
        if cls(card, 'not-open-tip'):
            continue
        course = cls(card, 'courseId', 'input')
        clazz = cls(card, 'clazzId', 'input')
        links = card.xpath('.//a/@href')
        if not course or not clazz or not links:
            continue
        cpi = parse_qs(urlparse(urljoin(BASE, links[0])).query).get('cpi', [''])[0]
        names = cls(card, 'course-name')
        if cpi and names:
            result.append({'courseId': course[0].get('value'), 'clazzId': clazz[0].get('value'), 'cpi': cpi, 'title': names[0].get('title') or plain(names[0])})
    return result
def folders(text):
    return list(dict.fromkeys(tree(text).xpath('.//ul[contains(@class,"file-list")]/li/@fileid')))
def points(text):
    result = []
    for node in tree(text).xpath('.//div[starts-with(@id,"cur")]'):
        match = re.fullmatch(r'cur(\d+)', node.get('id', ''))
        names = cls(node, 'clicktitle', 'a')
        if not match or not names:
            continue
        tips = ' '.join(plain(item) for item in cls(node, 'bntHoverTips'))
        result.append({'id': match[1], 'title': plain(names[0]), 'locked': '解锁' in tips, 'finished': '已完成' in tips})
    return result
def cards(text):
    if '章节未开放' in text:
        return [], {'notOpen': True}, ''
    match = re.search(r'\bmArg\s*=\s*(\{)', text)
    if not match:
        return [], {}, ''
    data, _ = json.JSONDecoder().raw_decode(text[match.start(1):])
    attachments = data.get('attachments') or []
    defaults = data.get('defaults') or {}
    result = []
    kinds = {'video':'video','audio':'audio','document':'document','ppt':'document','pdf':'document','read':'reading','readv2':'reading','workid':'work','work':'work','live':'live'}
    for item in attachments:
        prop = item.get('property') or {}
        kind = next((kinds[str(v).lower()] for v in [item.get('type'), prop.get('type'), prop.get('resourceType')] if str(v).lower() in kinds), 'unsupported')
        job = str(item.get('jobid') or prop.get('_jobid') or '')
        if not job:
            continue
        result.append({**item, 'property': prop, '_kind': kind, '_jobid': job, '_passed': flag(item.get('isPassed')) or kind == 'reading' and flag(prop.get('read')), '_defaults': defaults})
    return result, defaults, json.dumps(attachments, sort_keys=True, ensure_ascii=False) if attachments else ''

def work(text, paper):
    root = tree(text)
    forms = root.xpath('.//form')
    if not forms or '教师未创建完成该测验' in text:
        raise ValueError('教师尚未完成测验或页面没有作答表单')
    form = forms[0]
    saved = {}
    for element in form.xpath('.//input[@name]|.//textarea[@name]'):
        if element.get('type') in ('radio','checkbox') and element.get('checked') is None:
            continue
        saved[element.get('name')] = element.get('value', plain(element))
    if saved.get('stuStatus') == '5' or field(root,'stuStatus') == '5':
        raise ValueError('作业低于通过线，需要在官方页面处理')
    decode = decoder(text)
    questions, layouts = [], {}
    for block in cls(form, 'singleQuesId'):
        ident = block.get('data') or block.get('data-questionid')
        wrappers = cls(block, 'TiMu')
        if not ident or not wrappers:
            continue
        code = wrappers[0].get('data', '')
        kind = {'0':'single','1':'multiple','2':'blank','3':'judge','4':'essay'}.get(code, 'unsupported')
        titles = cls(block, 'Zy_TItle')
        title = decode(plain(titles[0]) if titles else '')
        title = re.sub(r'^\s*\d+[.、．]\s*|（\d+(?:\.\d+)?分）', '', title).strip()
        options = []
        for index, item in enumerate(block.xpath('.//ul[1]/li')):
            value = decode(item.get('aria-label') or plain(item)).strip()
            value = re.sub(r'选择$', '', value).strip()
            match = re.match(r'^([A-Z])(?:[.、．:：)\s]+)(.*)', value, re.S)
            label = match[1] if match else chr(65 + index)
            options.append({'id': label, 'text': match[2].strip() if match else value})
        names = block.xpath('.//input[starts-with(@name,$n)]/@name | .//textarea[starts-with(@name,$n)]/@name', n='answer'+ident)
        names = list(dict.fromkeys(name for name in names if not name.startswith('answertype')))
        raw = saved.get('answer'+ident, '')
        blanks = max(1, len(names)) if kind == 'blank' else 0
        if kind == 'multiple': answer = list(raw) if raw else []
        elif kind == 'blank': answer = [saved.get(name, '') for name in names] if names else ([raw] if raw else [])
        elif kind == 'judge': answer = ['true' if raw in ('true','1','正确') else 'false'] if raw in ('true','false','1','0','正确','错误') else []
        else: answer = [raw] if raw else []
        if any(not a for a in answer): answer = []
        images = [urljoin(BASE, src) for src in block.xpath('.//img/@src')]
        questions.append({'id': ident, 'text': title, 'kind': kind, 'options': options, 'answer': answer, 'images': images, 'blanks': blanks})
        layouts[ident] = {'code': code, 'fields': names, 'kind': kind}
    if not questions:
        raise ValueError('作业没有可识别的题目')
    ident = saved.get('workAnswerId') or saved.get('id') or saved.get('workId') or paper['id']
    return {'attempt': {'id': str(ident), 'paper_id': paper['id'], 'name': paper['name'], 'questions': questions, 'expires_at': None}, 'form': saved, 'layout': layouts, 'action': urljoin(BASE, form.get('action') or '/mooc-ans/work/addStudentWorkNew')}
