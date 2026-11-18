#!/usr/bin/env python3
"""briefing.json 검사 도구.

홈 화면 '아침 시황'에 쓰는 파일이 앱이 읽을 수 있는 형식인지 확인한다.
문제가 하나라도 있으면 그 내용을 출력하고 종료 코드 1로 끝난다. 푸시하기 전에 반드시 실행한다.

    python3 tools/check_briefing.py         # 저장소 폴더에서 실행
"""
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_DAYS = 30          # 파일에는 최근 30일까지만 둔다
MAX_SECTIONS = 10
MAX_POINTS = 10
MAX_POINT_LEN = 400
MAX_HEADLINE = 200
MAX_TITLE = 40
MAX_SOURCES = 20
# 투자 권유로 읽힐 수 있는 표현은 싣지 않는다
ADVICE = re.compile(r'(매수\s*추천|매도\s*추천|강력\s*매수|사야\s*합니다|파세요|사세요|매수하세요|매도하세요|추천\s*종목|지금이\s*기회)')


def text_ok(x, limit):
    return isinstance(x, str) and x.strip() != '' and len(x) <= limit


def main():
    path = os.path.join(ROOT, 'briefing.json')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print('오류: briefing.json을 읽을 수 없습니다:', e)
        return 1
    if not isinstance(data, dict) or not isinstance(data.get('days'), list):
        print('오류: 최상위에 "days" 목록이 있어야 합니다')
        return 1

    errors = []
    days = data['days']
    if len(days) > MAX_DAYS:
        errors.append('날짜 묶음이 %d개입니다. 오래된 것을 지워 최근 %d일까지만 두세요' % (len(days), MAX_DAYS))
    last = ''
    for di, day in enumerate(days):
        where = '날짜 #%d' % (di + 1)
        if not isinstance(day, dict):
            errors.append(where + ': 묶음이 { } 모양이 아닙니다')
            continue
        date = day.get('date')
        try:
            datetime.date.fromisoformat(date)
            where = date
        except Exception:
            errors.append('%s: date 값이 YYYY-MM-DD 날짜가 아닙니다: %r' % (where, date))
            date = ''
        if date and date <= last:
            errors.append('%s: 앞의 날짜(%s)보다 늦어야 합니다 (같은 날을 두 번 넣지 않습니다)' % (where, last))
        last = date or last
        made = day.get('made_at')
        if made is not None:
            try:
                datetime.datetime.fromisoformat(str(made).replace('Z', '+00:00'))
            except Exception:
                errors.append('%s: made_at 이 시각 형식이 아닙니다: %r' % (where, made))
        if not text_ok(day.get('headline'), MAX_HEADLINE):
            errors.append('%s: headline(한 줄 요약)이 없거나 %d자를 넘습니다' % (where, MAX_HEADLINE))
        sections = day.get('sections')
        if not isinstance(sections, list) or not sections or len(sections) > MAX_SECTIONS:
            errors.append('%s: sections 는 1~%d개의 목록이어야 합니다' % (where, MAX_SECTIONS))
            sections = []
        texts = [day.get('headline') or '']
        for si, sec in enumerate(sections):
            at = '%s 구역 #%d' % (where, si + 1)
            if not isinstance(sec, dict) or not text_ok(sec.get('title'), MAX_TITLE):
                errors.append(at + ': title 이 없거나 너무 깁니다')
                continue
            pts = sec.get('points')
            if not isinstance(pts, list) or not pts or len(pts) > MAX_POINTS:
                errors.append('%s(%s): points 는 1~%d개의 목록이어야 합니다' % (at, sec['title'], MAX_POINTS))
                continue
            for pi, pt in enumerate(pts):
                if not text_ok(pt, MAX_POINT_LEN):
                    errors.append('%s(%s) #%d: 비어 있거나 %d자를 넘습니다' % (at, sec['title'], pi + 1, MAX_POINT_LEN))
                else:
                    texts.append(pt)
        sources = day.get('sources', [])
        if not isinstance(sources, list) or len(sources) > MAX_SOURCES:
            errors.append('%s: sources 는 %d개 이하의 목록이어야 합니다' % (where, MAX_SOURCES))
            sources = []
        if date and not sources:
            errors.append('%s: 출처(sources)가 하나도 없습니다. 숫자를 가져온 곳을 적어야 합니다' % where)
        for so in sources:
            if not isinstance(so, dict) or not text_ok(so.get('name'), 60) or not str(so.get('url', '')).startswith('https://'):
                errors.append('%s: 출처는 { "name": "...", "url": "https://..." } 모양이어야 합니다: %r' % (where, so))
        for t in texts:
            if re.search(r'<[a-zA-Z/]', t):
                errors.append('%s: HTML 태그처럼 보이는 글자가 있습니다 → %s' % (where, t[:40]))
            m = ADVICE.search(t)
            if m:
                errors.append('%s: 투자 권유로 읽힐 수 있는 표현("%s")이 있습니다 → %s' % (where, m.group(0), t[:40]))

    if errors:
        print('문제가 %d개 있습니다. 고친 뒤 다시 실행하세요.\n' % len(errors))
        for e in errors:
            print(' -', e)
        return 1
    print('이상 없음. 날짜 %d개, 가장 최근 %s' % (len(days), last or '-'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
