#!/usr/bin/env python3
"""Post one Design Simplified episode from the GitHub repo (cloud version). Standard library only.

  python3 publish/publish_cloud.py --ep 11 --dry-run
  python3 publish/publish_cloud.py --ep 11 --youtube --instagram

Secrets come from environment variables set on the cloud environment (never from files):
  YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN
  INSTAGRAM_ACCOUNT_ID, META_PAGE_TOKEN, META_GRAPH_VERSION (optional, default v26.0)

Instagram downloads episodes/<folder>/instagram.mp4 from the public repo branch, so no GitHub token is needed.
Writes episodes/<folder>/posted.json; the caller commits and pushes it.
A file named episodes/HOLD in the repo skips the run.
"""
import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OWNER, REPO = 'ShubhankarKate', 'design-simplified'
BRANCH = os.environ.get('GIT_BRANCH', 'episodes')
RAW = f'https://raw.githubusercontent.com/{OWNER}/{REPO}/{BRANCH}'


def die(msg):
    print('ERROR:', msg)
    sys.exit(1)


def need(name):
    val = os.environ.get(name, '').strip()
    if not val:
        die(f'environment variable {name} is not set')
    return val


def http(method, url, data=None, headers=None, raw=None, timeout=300):
    """Return (status, headers, parsed-json-or-text). data=dict -> form-encoded; raw=bytes -> body as-is."""
    headers = dict(headers or {})
    body = None
    if raw is not None:
        body = raw
    elif data is not None:
        if headers.get('Content-Type') == 'application/json':
            body = json.dumps(data).encode()
        else:
            body = urllib.parse.urlencode(data).encode()
            headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode('utf-8', 'replace')
            status, hdrs = r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        text = e.read().decode('utf-8', 'replace')
        status, hdrs = e.code, dict(e.headers)
    except urllib.error.URLError as e:
        die(f'could not reach {urllib.parse.urlparse(url).netloc}: {e.reason}')
    try:
        return status, hdrs, json.loads(text)
    except ValueError:
        return status, hdrs, text


def episode_dir(ep):
    hits = sorted(glob.glob(os.path.join(ROOT, 'episodes', f'ep{int(ep):02d}-*')))
    if not hits:
        die(f'no folder episodes/ep{int(ep):02d}-* in the repo')
    return hits[0]


def parse_youtube_txt(path):
    """Sections start with an all-caps header line: TITLE, DESCRIPTION, SETTINGS, THUMBNAIL."""
    sections, cur = {}, None
    for line in open(path, encoding='utf-8').read().splitlines():
        m = re.match(r'^(TITLE|DESCRIPTION|SETTINGS|THUMBNAIL)\b', line)
        if m:
            cur = m.group(1)
            sections[cur] = []
            continue
        if cur:
            sections[cur].append(line)
    title = '\n'.join(sections.get('TITLE', [])).strip()
    desc = '\n'.join(sections.get('DESCRIPTION', [])).strip()
    if not title or not desc:
        die(f'{path}: TITLE or DESCRIPTION section missing')
    if len(title) > 100:
        die(f'YouTube title is {len(title)} characters; the limit is 100')
    if len(desc.encode('utf-8')) > 5000:
        die('YouTube description is over 5,000 bytes')
    # YouTube rejects '<' and '>'; use look-alike characters.
    safe = str.maketrans({'<': '‹', '>': '›'})
    title, desc = title.translate(safe), desc.translate(safe)
    tags = re.findall(r'#(\w+)', desc)
    return title, desc, tags


def load_posted(folder):
    p = os.path.join(folder, 'posted.json')
    return json.load(open(p)) if os.path.exists(p) else {}


def save_posted(folder, posted):
    p = os.path.join(folder, 'posted.json')
    tmp = p + '.tmp'
    json.dump(posted, open(tmp, 'w'), indent=2)
    os.replace(tmp, p)


# ---------- YouTube ----------

def yt_access_token():
    s, _, r = http('POST', 'https://oauth2.googleapis.com/token', data={
        'client_id': need('YOUTUBE_CLIENT_ID'), 'client_secret': need('YOUTUBE_CLIENT_SECRET'),
        'refresh_token': need('YOUTUBE_REFRESH_TOKEN'), 'grant_type': 'refresh_token'})
    if s != 200 or 'access_token' not in r:
        die(f'YouTube token refresh failed ({s}): {r}')
    return r['access_token']


def post_youtube(folder, dry):
    title, desc, tags = parse_youtube_txt(os.path.join(folder, 'youtube.txt'))
    video = os.path.join(folder, 'youtube-short.mp4')
    if not os.path.exists(video):
        video = os.path.join(folder, 'video.mp4')
    size = os.path.getsize(video)
    meta = {
        'snippet': {'title': title, 'description': desc, 'tags': tags, 'categoryId': '27'},  # 27 = Education
        'status': {'privacyStatus': 'public', 'selfDeclaredMadeForKids': False, 'embeddable': True},
    }
    print(f'YouTube: "{title}" · {os.path.basename(video)} {size / 1e6:.1f} MB · public · not made for kids')
    if dry:
        print('  (dry run: nothing uploaded)')
        return None
    token = yt_access_token()
    s, h, r = http('POST',
                   'https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status',
                   data=meta, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
                                       'X-Upload-Content-Type': 'video/mp4',
                                       'X-Upload-Content-Length': str(size)})
    loc = h.get('Location') or h.get('location')
    if s != 200 or not loc:
        die(f'YouTube upload session failed ({s}): {r}')
    with open(video, 'rb') as f:
        s, _, r = http('PUT', loc, raw=f.read(), headers={'Content-Type': 'video/mp4'}, timeout=900)
    if s not in (200, 201) or 'id' not in r:
        die(f'YouTube upload failed ({s}): {r}')
    vid = r['id']
    url = f'https://www.youtube.com/shorts/{vid}'
    print('  posted:', url)
    return {'id': vid, 'url': url, 'title': title, 'posted_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}


# ---------- Instagram (Graph API, video_url) ----------

def post_instagram(folder, dry):
    rel = os.path.relpath(folder, ROOT).replace(os.sep, '/')
    caption = open(os.path.join(folder, 'caption.txt'), encoding='utf-8').read().strip()
    if len(caption) > 2200:
        die(f'Instagram caption is {len(caption)} characters; the limit is 2,200')
    if caption.count('#') > 30:
        die('Instagram allows at most 30 hashtags')
    local = os.path.join(folder, 'instagram.mp4')
    if not os.path.exists(local):
        die(f'{rel}/instagram.mp4 is missing; it must be committed before posting')
    video_url = f'{RAW}/{rel}/instagram.mp4'
    s, _, _ = http('HEAD', video_url)
    if s != 200:
        die(f'{video_url} is not reachable yet ({s}); push the branch and retry')
    first = caption.splitlines()[0] if caption else ''
    print(f'Instagram: Reel · {os.path.getsize(local) / 1e6:.1f} MB · caption {len(caption)} chars · "{first[:80]}"')
    if dry:
        print('  (dry run: nothing uploaded)')
        return None

    ig_id, tok = need('INSTAGRAM_ACCOUNT_ID'), need('META_PAGE_TOKEN')
    v = os.environ.get('META_GRAPH_VERSION', 'v26.0').strip()
    base = f'https://graph.facebook.com/{v}'
    data = {'media_type': 'REELS', 'video_url': video_url, 'caption': caption,
            'share_to_feed': 'true', 'access_token': tok,
            'collaborators': json.dumps(['_sillykate'])}
    s, _, r = http('POST', f'{base}/{ig_id}/media', data=data)
    if s != 200 or 'id' not in r:
        print(f'  collab invite rejected ({s}); retrying without it')
        data.pop('collaborators')
        s, _, r = http('POST', f'{base}/{ig_id}/media', data=data)
    if s != 200 or 'id' not in r:
        die(f'Instagram container failed ({s}): {r}')
    cid = r['id']

    status = None
    for _ in range(40):  # up to ~10 minutes
        time.sleep(15)
        s, _, r = http('GET', f'{base}/{cid}?fields=status_code&access_token={urllib.parse.quote(tok)}')
        status = r.get('status_code') if isinstance(r, dict) else None
        print('  processing:', status)
        if status == 'FINISHED':
            break
        if status in ('ERROR', 'EXPIRED'):
            die(f'Instagram processing failed: {r}')
    if status != 'FINISHED':
        die('Instagram is still processing after 10 minutes; re-run later')

    s, _, r = http('POST', f'{base}/{ig_id}/media_publish', data={'creation_id': cid, 'access_token': tok})
    if s != 200 or 'id' not in r:
        die(f'Instagram publish failed ({s}): {r}')
    mid = r['id']
    s, _, r = http('GET', f'{base}/{mid}?fields=permalink&access_token={urllib.parse.quote(tok)}')
    url = r.get('permalink') if isinstance(r, dict) else None
    print('  posted:', url or mid)

    fc = os.path.join(folder, 'first_comment.txt')
    if os.path.exists(fc):
        msg = open(fc, encoding='utf-8').read().strip()
        if msg:
            s, _, r = http('POST', f'{base}/{mid}/comments', data={'message': msg, 'access_token': tok})
            print('  first comment:', 'posted' if s == 200 else f'not posted ({s}); paste it by hand')
    return {'id': mid, 'url': url, 'posted_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ep', required=True)
    ap.add_argument('--youtube', action='store_true')
    ap.add_argument('--instagram', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if not (a.youtube or a.instagram or a.dry_run):
        die('say --youtube and/or --instagram (or --dry-run)')
    if os.path.exists(os.path.join(ROOT, 'episodes', 'HOLD')):
        die('episodes/HOLD exists: skipping this run')
    folder = episode_dir(a.ep)
    posted = load_posted(folder)
    print('Episode folder:', os.path.relpath(folder, ROOT))
    for name, fn, on in (('youtube', post_youtube, a.youtube or a.dry_run),
                         ('instagram', post_instagram, a.instagram or a.dry_run)):
        if not on:
            continue
        if name in posted:
            print(f'{name}: already posted ({posted[name].get("url")}); skipping')
            continue
        res = fn(folder, a.dry_run)
        if res:
            posted[name] = res
            save_posted(folder, posted)


if __name__ == '__main__':
    main()
