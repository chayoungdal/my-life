#!/usr/bin/env python3
"""맥북의 아침 시황 예약이 돌지 못한 날을 위한 '간이 시황'을 만든다.

- 사람이 쓴 해설·전망 없이, 숫자와 기사 제목만으로 briefing.json 의 오늘 묶음을 채운다.
  지수와 내 종목은 stocks-data.json(먼저 tools/fetch_stocks.py 를 돌려 새로 받는다), 환율은 네이버 금융,
  기사 제목은 매일경제 증권 RSS 에서 가져온다.
- 오늘 묶음이 이미 있고 그것이 정식 정리("auto" 가 없는 것)이면 아무것도 하지 않는다.
  오늘 묶음이 없거나 간이 정리("auto": true)이면 새로 만든다.
- 맥북의 예약 작업은 "auto": true 인 묶음을 보면 정식 정리로 바꿔 쓴다.
- 외부 패키지 없이 파이썬 기본 기능만 쓴다. 투자 권유로 읽힐 수 있는 제목은 싣지 않는다.

    python3 tools/auto_briefing.py           # 저장소 폴더에서 실행
"""
import datetime
import email.utils
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_briefing import ADVICE  # noqa: E402  (같은 금지 표현을 쓴다)
from notify_stocks import last_final_index  # noqa: E402  (장이 끝나 확정된 봉 찾기)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = datetime.timezone(datetime.timedelta(hours=9))
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
FX_URL = 'https://api.stock.naver.com/marketindex/exchange/FX_USDKRW/prices?page=1&pageSize=3'
NEWS = ('매일경제', 'https://www.mk.co.kr/rss/50200011/')
MAX_AGE_DAYS = 5      # 이보다 오래된 봉은 '가장 최근 장'으로 쓰지 않는다
NEWS_HOURS = 20       # 이 시간 안에 나온 기사 제목만 싣는다
NEWS_COUNT = 3


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def md(d):  # '20261126' → '26일'
    return '%d일' % int(d[6:8])


def num(v, digits=2):
    return ('%.' + str(digits) + 'f') % v if digits else '%d' % round(v)


def comma(v, digits=2):
    return format(round(v, digits), ',.%df' % digits)


def pct(v):
    return '%+.2f%%' % v


def final_bar(it, now):
    """확정된 가장 최근 봉의 (날짜, 종가, 등락률). 없거나 너무 오래됐으면 None."""
    k = last_final_index(it, now)
    if k is None or k < 1:
        return None
    day = datetime.datetime.strptime(it['d'][k], '%Y%m%d').date()
    if (now.date() - day).days > MAX_AGE_DAYS:
        return None
    return it['d'][k], it['c'][k], (it['c'][k] / it['c'][k - 1] - 1) * 100


def market_section(items, now, market, names):
    """지수 한 줄 + 내 종목 가운데 많이 움직인 것. (구역, 머리말에 쓸 조각)"""
    by_id = {it['id']: it for it in items}
    idx, date = [], None
    for iid, label in names:
        bar = final_bar(by_id[iid], now) if iid in by_id else None
        if bar:
            date = date or bar[0]
            idx.append((label, bar[1], bar[2]))
    if not idx:
        return None, ''
    where = '(현지) ' if market == 'US' else ' '
    points = ['%s%s%s' % (md(date), where, ', '.join('%s %s(%s)' % (n, comma(c), pct(p)) for n, c, p in idx)) + '로 마감했습니다.']
    moves = []
    for it in items:
        if it.get('kind') == 'index' or (it.get('market') == 'KR') != (market == 'KR'):
            continue
        bar = final_bar(it, now)
        if bar and bar[0] == date:
            moves.append((it['name'], bar[2]))
    ups = sorted([m for m in moves if m[1] > 0], key=lambda m: -m[1])[:4]
    downs = sorted([m for m in moves if m[1] < 0], key=lambda m: m[1])[:4]
    if ups:
        points.append('내 종목 가운데 많이 오른 것: ' + ', '.join('%s %s' % (n, pct(p)) for n, p in ups))
    if downs:
        points.append('내 종목 가운데 많이 내린 것: ' + ', '.join('%s %s' % (n, pct(p)) for n, p in downs))
    if moves:
        up = len([m for m in moves if m[1] > 0])
        points.append('내 종목 %d개 가운데 %d개가 오르고 %d개가 내렸습니다.' % (len(moves), up, len([m for m in moves if m[1] < 0])))
    head = '%s %s' % (idx[0][0], pct(idx[0][2]))
    return {'title': '미국 증시' if market == 'US' else '한국 증시', 'points': points}, head


def fx_section():
    try:
        rows = json.loads(get(FX_URL).decode('utf-8'))
        r = rows[0]
        day, close, diff = str(r['localTradedAt']), str(r['closePrice']), float(str(r['fluctuations']).replace(',', ''))
        float(close.replace(',', ''))
        d = datetime.date.fromisoformat(day[:10])
        how = '변동이 없었습니다' if diff == 0 else '%s원 %s' % (comma(abs(diff)), '올랐습니다' if diff > 0 else '내렸습니다')
        return {'title': '환율', 'points': ['%d일 원/달러 환율은 %s원입니다. 전날보다 %s.' % (d.day, close, how)]}
    except Exception as e:
        print('환율을 받지 못했습니다:', str(e)[:80])
        return None


def news_section(now):
    name, url = NEWS
    try:
        root = ET.fromstring(get(url))
    except Exception as e:
        print('기사 제목을 받지 못했습니다:', str(e)[:80])
        return None, []
    points, sources = [], []
    for item in root.iter('item'):
        title = html.unescape(re.sub(r'<[^>]*>', '', (item.findtext('title') or ''))).strip()
        title = re.sub(r'[<>]', '', re.sub(r'\s+', ' ', title))
        link = (item.findtext('link') or '').strip()
        when = item.findtext('pubDate') or ''
        try:
            age = (now - email.utils.parsedate_to_datetime(when)).total_seconds() / 3600
        except Exception:
            age = None
        if not title or len(title) > 120 or not link.startswith('https://') or ADVICE.search(title):
            continue
        if age is not None and not (0 <= age <= NEWS_HOURS):
            continue
        points.append('%s (%s)' % (title, name))
        sources.append({'name': '%s: %s' % (name, title[:40]), 'url': link})
        if len(points) >= NEWS_COUNT:
            break
    if not points:
        return None, []
    return {'title': '주요 뉴스', 'points': points}, sources


def main():
    now = datetime.datetime.now(KST)
    today = now.strftime('%Y-%m-%d')
    path = os.path.join(ROOT, 'briefing.json')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        days = data['days']
    except Exception as e:
        print('오류: briefing.json 을 읽을 수 없습니다:', e)
        return 1
    cur = next((d for d in days if d.get('date') == today), None)
    if cur and not cur.get('auto'):
        print('오늘(%s) 정식 정리가 이미 있습니다. 간이 정리를 만들지 않습니다.' % today)
        return 0
    try:
        with open(os.path.join(ROOT, 'stocks-data.json'), encoding='utf-8') as f:
            items = json.load(f)['items']
    except Exception as e:
        print('오류: stocks-data.json 을 읽을 수 없습니다:', e)
        return 1

    us, us_head = market_section(items, now, 'US', [('.DJI', '다우'), ('.INX', 'S&P500'), ('.IXIC', '나스닥')])
    kr, kr_head = market_section(items, now, 'KR', [('KOSPI', '코스피'), ('KOSDAQ', '코스닥')])
    if not us and not kr:
        print('확정된 최근 시세가 없어 간이 정리를 만들지 않습니다.')
        return 0
    fx = fx_section()
    news, news_src = news_section(now)
    sections = [s for s in (us, kr, fx, news) if s]
    sections.append({'title': '안내', 'points': [
        '맥북의 정식 정리가 아직 올라오지 않아 자동으로 만든 간이 정리입니다. 숫자와 기사 제목만 모았고, 해설과 전망은 없습니다.',
        '정식 정리가 만들어지면 이 내용은 자동으로 바뀝니다. 투자 권유가 아니며, 숫자는 출처에서 다시 확인하세요.',
    ]})
    day = {
        'date': today,
        'made_at': now.strftime('%Y-%m-%dT%H:%M:%S+09:00'),
        'auto': True,
        'headline': '[간이] ' + ', '.join(h for h in ((us_head and '뉴욕 ' + us_head), (kr_head and '전일 ' + kr_head)) if h),
        'sections': sections,
        'sources': [{'name': '네이버 금융(지수·종목·환율)', 'url': 'https://finance.naver.com/sise/'}] + news_src,
    }
    days = [d for d in days if d.get('date') != today] + [day]
    days.sort(key=lambda d: d.get('date', ''))
    data['days'] = days[-30:]
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print('간이 정리를 넣었습니다: %s · %s' % (today, day['headline']))
    for s in sections[:-1]:
        print(' -', s['title'], '·', s['points'][0][:70])
    return 0


if __name__ == '__main__':
    sys.exit(main())
