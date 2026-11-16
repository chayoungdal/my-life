#!/usr/bin/env python3
"""learn-extra.json 검사 도구.

'오늘의 배움'에 매주 더해지는 내용 파일이 앱이 읽을 수 있는 형식인지 확인한다.
문제가 하나라도 있으면 그 내용을 출력하고 종료 코드 1로 끝난다. 푸시하기 전에 반드시 실행한다.

    python3 tools/check_learn.py            # 저장소 폴더에서 실행
"""
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABS = {'lit': 3, 'fact': 0, 'joke': 2, 'quote': 2, 'en': 4}  # 항목 하나의 칸 수 (fact는 문장 하나)
LEVELS = ('1', '2', '3')
MAX_PER_LIST = 50
MAX_LEN = {'fact': 300}
MAX_FIELD = 400


def key_of(tab, item):
    text = item if tab == 'fact' else item[0]
    return re.sub(r'\s+', ' ', text).strip()


def main():
    errors = []
    path = os.path.join(ROOT, 'learn-extra.json')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:  # 파일이 없거나 JSON이 아님
        print('오류: learn-extra.json을 읽을 수 없습니다:', e)
        return 1
    try:
        with open(os.path.join(ROOT, 'index.html'), encoding='utf-8') as f:
            base = f.read()
    except Exception:
        base = ''

    if not isinstance(data, dict) or not isinstance(data.get('weeks'), list):
        print('오류: 최상위에 "weeks" 목록이 있어야 합니다')
        return 1

    seen = {tab: {} for tab in TABS}
    last_week = ''
    counts = []
    for wi, week in enumerate(data['weeks']):
        where = '주 #%d' % (wi + 1)
        if not isinstance(week, dict):
            errors.append(where + ': 묶음이 { } 모양이 아닙니다')
            continue
        w = week.get('week')
        try:
            day = datetime.date.fromisoformat(w)
            if day.weekday() != 0:
                errors.append('%s: week "%s"는 월요일이 아닙니다' % (where, w))
        except Exception:
            errors.append('%s: week 값이 YYYY-MM-DD 날짜가 아닙니다: %r' % (where, w))
            w = ''
        if w and w <= last_week:
            errors.append('%s: week "%s"가 앞의 주("%s")보다 늦어야 합니다 (같은 주를 두 번 넣지 않습니다)' % (where, w, last_week))
        last_week = w or last_week
        items = week.get('items')
        if not isinstance(items, dict):
            errors.append(where + ': "items"가 없습니다')
            continue
        for tab in items:
            if tab not in TABS:
                errors.append('%s: 모르는 분야 "%s" (lit, fact, joke, quote, en 만 가능)' % (where, tab))
        n = 0
        for tab, fields in TABS.items():
            by_level = items.get(tab, {})
            if not isinstance(by_level, dict):
                errors.append('%s %s: 단계별 { "1": [...], "2": [...], "3": [...] } 모양이어야 합니다' % (where, tab))
                continue
            for lv in by_level:
                if lv not in LEVELS:
                    errors.append('%s %s: 모르는 단계 "%s" ("1", "2", "3" 만 가능)' % (where, tab, lv))
            for lv in LEVELS:
                lst = by_level.get(lv, [])
                if not isinstance(lst, list):
                    errors.append('%s %s 단계 %s: 목록이 아닙니다' % (where, tab, lv))
                    continue
                if len(lst) > MAX_PER_LIST:
                    errors.append('%s %s 단계 %s: 한 번에 %d개까지만 넣을 수 있습니다' % (where, tab, lv, MAX_PER_LIST))
                for ii, item in enumerate(lst):
                    at = '%s %s 단계 %s #%d' % (where, tab, lv, ii + 1)
                    if tab == 'fact':
                        ok = isinstance(item, str) and item.strip() and len(item) <= MAX_LEN['fact']
                    else:
                        ok = (isinstance(item, list) and len(item) == fields
                              and all(isinstance(x, str) and x.strip() and len(x) <= MAX_FIELD for x in item))
                    if not ok:
                        errors.append(at + ': 형식이 맞지 않습니다 (칸 수, 빈 칸, 길이를 확인)')
                        continue
                    k = key_of(tab, item)
                    if k in seen[tab]:
                        errors.append('%s: 이미 있는 내용입니다 (%s) → %s' % (at, seen[tab][k], k[:40]))
                        continue
                    if base and k in base:
                        errors.append('%s: 앱 기본 내용에 이미 있습니다 → %s' % (at, k[:40]))
                        continue
                    seen[tab][k] = at
                    n += 1
        counts.append('%s: %d개' % (w or '?', n))

    if errors:
        print('문제가 %d개 있습니다. 고친 뒤 다시 실행하세요.\n' % len(errors))
        for e in errors:
            print(' -', e)
        return 1
    total = sum(len(v) for v in seen.values())
    print('이상 없음. 주 %d개, 항목 %d개' % (len(data['weeks']), total))
    for c in counts[-5:]:
        print('  ', c)
    return 0


if __name__ == '__main__':
    sys.exit(main())
