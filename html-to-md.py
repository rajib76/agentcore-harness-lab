"""Convert the AgentCore harness lab HTML into Markdown.

The page uses a small, closed vocabulary of layout classes (col, code, tabs,
tw, goal/note/warn/tip, ck, lab-head). A generic converter mangles the tab
groups and drops the callout semantics, so this walks the tree and maps each
construct deliberately.
"""
import html
import re
import sys
from html.parser import HTMLParser

VOID = {'br', 'img', 'input', 'hr', 'meta', 'link', 'source', 'area', 'base',
        'col', 'embed', 'param', 'track', 'wbr'}
SKIP_TAGS = {'script', 'style', 'nav', 'svg'}


class Node:
    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.kids = []

    def cls(self):
        return self.attrs.get('class', '').split()

    def has(self, c):
        return c in self.cls()

    def find(self, tag=None, c=None):
        out = []
        for k in self.kids:
            if isinstance(k, Node):
                if (tag is None or k.tag == tag) and (c is None or k.has(c)):
                    out.append(k)
                out.extend(k.find(tag, c))
        return out

    def text(self):
        parts = []
        for k in self.kids:
            parts.append(k if isinstance(k, str) else k.text())
        return ''.join(parts)


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        n = Node(tag, attrs)
        self.stack[-1].kids.append(n)
        if tag not in VOID:
            self.stack.append(n)

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self.skip:
            self.stack[-1].kids.append(data)


# ---------- inline rendering ----------

def esc(t):
    # `_` is deliberately absent: GFM ignores intra-word underscores, so
    # escaping them only turns EXPORT_NOTES.md into EXPORT\_NOTES.md.
    return re.sub(r'([\\`*\[\]])', r'\\\1', t)


def inline(node):
    """Render inline content, preserving code/bold/italic/links."""
    out = []
    for k in node.kids:
        if isinstance(k, str):
            out.append(esc(re.sub(r'\s+', ' ', k)))
            continue
        if k.tag == 'code':
            out.append('`' + re.sub(r'\s+', ' ', k.text()).strip() + '`')
        elif k.tag in ('b', 'strong'):
            inner = inline(k).strip()
            out.append('**' + inner + '**' if inner else '')
        elif k.tag in ('em', 'i'):
            inner = inline(k).strip()
            out.append('*' + inner + '*' if inner else '')
        elif k.tag == 'a':
            href = k.attrs.get('href', '')
            out.append('[' + inline(k).strip() + '](' + href + ')')
        elif k.tag == 'br':
            out.append('  \n')
        else:
            out.append(inline(k))
    return ''.join(out)


def para(node):
    return re.sub(r' +', ' ', inline(node)).strip()


# ---------- block rendering ----------

def code_block(node):
    """div.code -> fenced block. The code-bar label carries lang + a note."""
    lang, note = '', ''
    bars = node.find('span', 'code-lang')
    if bars:
        label = bars[0].text().strip()
        # e.g. "bash · needs Node 20+"  /  "python · invoke.py"
        parts = re.split(r'\s*[··]\s*', label, maxsplit=1)
        lang = parts[0].strip()
        note = parts[1].strip() if len(parts) > 1 else ''
    pres = node.find('pre')
    body = html.unescape(pres[0].text()) if pres else ''
    body = body.strip('\n')
    fence = '```'
    while fence in body:
        fence += '`'
    out = []
    if note:
        out.append('*%s*' % note)
        out.append('')
    out.append(fence + (lang if re.fullmatch(r'[a-zA-Z0-9+-]+', lang) else ''))
    out.append(body)
    out.append(fence)
    return '\n'.join(out)


def table_block(node):
    tbl = node if node.tag == 'table' else (node.find('table') or [None])[0]
    if tbl is None:
        return ''
    heads = [para(th) for th in tbl.find('th')]
    rows = []
    for tr in tbl.find('tr'):
        tds = [k for k in tr.kids if isinstance(k, Node) and k.tag == 'td']
        if tds:
            rows.append([para(td).replace('|', '\\|') for td in tds])
    if not heads and not rows:
        return ''
    if not heads:
        heads = [''] * len(rows[0])
    width = len(heads)
    out = ['| ' + ' | '.join(h.replace('|', '\\|') for h in heads) + ' |',
           '|' + '|'.join(['---'] * width) + '|']
    for r in rows:
        r = (r + [''] * width)[:width]
        out.append('| ' + ' | '.join(r) + ' |')
    return '\n'.join(out)


CALLOUT = {'goal': 'Goal', 'note': 'Note', 'warn': 'Warning', 'tip': 'Tip'}


def callout(node, kind):
    lines = []
    label = CALLOUT[kind]
    bs = [k for k in node.kids if isinstance(k, Node) and k.tag == 'b']
    title = para(bs[0]) if bs else ''
    # The <b> is rendered by CSS as a heading; strip it from the flow.
    body = []
    for k in node.kids:
        if isinstance(k, Node) and k is (bs[0] if bs else None):
            continue
        if isinstance(k, Node) and k.tag == 'p':
            body.append(para(k))
        elif isinstance(k, str) and k.strip():
            body.append(esc(k.strip()))
    head = '**%s — %s**' % (label, title) if title else '**%s**' % label
    if kind == 'goal' and not title:
        # goal paragraphs embed the word "Goal" in a <strong>
        body = [re.sub(r'^\*\*Goal\*\*\s*', '', b) for b in body]
    lines.append('> ' + head)
    for b in body:
        if b:
            lines.append('>')
            lines.append('> ' + b)
    return '\n'.join(lines)


def checkpoint(node):
    body = node.find('div', 'ck-body')
    if not body:
        return ''
    b = body[0]
    title, rest = '', []
    for k in b.kids:
        if isinstance(k, Node) and k.tag == 'p':
            if k.has('ck-t'):
                title = para(k)
            else:
                rest.append(para(k))
    out = ['> **%s**' % (title or 'Checkpoint')]
    for r in rest:
        out.append('>')
        out.append('> ' + r)
    return '\n'.join(out)


def tabs_block(node, depth):
    labels = [para(b) for b in node.find('button')]
    panels = [k for k in node.find('div') if k.has('tabpanel')]
    out = []
    for i, p in enumerate(panels):
        lab = labels[i] if i < len(labels) else 'Option %d' % (i + 1)
        out.append('#' * min(depth + 2, 6) + ' ' + lab)
        out.append('')
        out.append(render(p, depth + 1))
    return '\n\n'.join(x for x in out if x is not None)


def render(node, depth=2):
    """Render a container node's children as Markdown blocks."""
    blocks = []

    def emit(s):
        if s and s.strip():
            blocks.append(s.strip('\n'))

    for k in node.kids:
        if isinstance(k, str):
            if k.strip():
                emit(esc(k.strip()))
            continue
        c = k.cls()
        if k.tag == 'section':
            emit(section_block(k))
        elif k.tag == 'div' and 'lab-head' in c:
            emit(lab_head(k, depth))
        elif k.tag == 'div' and 'code' in c:
            emit(code_block(k))
        elif k.tag == 'div' and 'tw' in c:
            emit(table_block(k))
        elif k.tag == 'table':
            emit(table_block(k))
        elif k.tag == 'div' and 'tabs' in c:
            emit(tabs_block(k, depth))
        elif k.tag == 'div' and 'ck' in c:
            emit(checkpoint(k))
        elif k.tag == 'div' and any(x in CALLOUT for x in c):
            kind = next(x for x in c if x in CALLOUT)
            emit(callout(k, kind))
        elif k.tag == 'div':
            emit(render(k, depth))
        elif k.tag == 'p':
            if 'step' in c:
                emit('#' * min(depth + 1, 6) + ' ' + para(k))
            elif 'kicker' in c:
                emit('## ' + para(k))
            elif 'lab-sub' in c:
                emit('*' + para(k) + '*')
            else:
                emit(para(k))
        elif k.tag in ('h1', 'h2', 'h3', 'h4', 'h5'):
            lvl = {'h1': 1, 'h2': depth, 'h3': depth + 1,
                   'h4': depth + 1, 'h5': depth + 2}[k.tag]
            emit('#' * min(lvl, 6) + ' ' + para(k))
        elif k.tag in ('ul', 'ol'):
            items = []
            for i, li in enumerate(k.find('li'), 1):
                bullet = '- ' if k.tag == 'ul' else '%d. ' % i
                items.append(bullet + para(li))
            emit('\n'.join(items))
        elif k.tag == 'pre':
            emit('```\n' + html.unescape(k.text()).strip('\n') + '\n```')
        else:
            emit(render(k, depth))
    return '\n\n'.join(blocks)


def lab_head(node, depth):
    h2 = node.find('h2')
    n = node.find('div', 'lab-n')
    sub = node.find('p', 'lab-sub')
    num = n[0].text().strip() if n else ''
    title = para(h2[0]) if h2 else ''
    head = '## ' + (('%s. %s' % (num, title)) if num and num.isdigit() else title)
    out = [head]
    if sub:
        out.append('')
        out.append('*' + para(sub[0]) + '*')
    return '\n'.join(out)


def section_block(sec):
    return render(sec, 2)


def masthead(root):
    heads = root.find('header', 'mast')
    if not heads:
        return ''
    m = heads[0]
    out = []
    h1 = m.find('h1')
    if h1:
        out.append('# ' + para(h1[0]))
    eb = m.find('p', 'eyebrow')
    if eb:
        out.append('*' + para(eb[0]) + '*')
    sf = m.find('p', 'standfirst')
    if sf:
        out.append(para(sf[0]))
    facts = []
    for f in m.find('div', 'fact'):
        dt = f.find('dt')
        dd = f.find('dd')
        if dt and dd:
            facts.append((para(dt[0]), para(dd[0])))
    if facts:
        out.append('| ' + ' | '.join(k for k, _ in facts) + ' |')
        out.append('|' + '|'.join(['---'] * len(facts)) + '|')
        out.append('| ' + ' | '.join(v for _, v in facts) + ' |')
    return '\n\n'.join(out[:3]) + ('\n\n' + '\n'.join(out[3:]) if len(out) > 3 else '')


def slug(text):
    s = re.sub(r'[^\w\s-]', '', text.lower().replace('\\', ''))
    return re.sub(r'\s+', '-', s.strip())


def toc(body):
    lines = []
    in_fence = False
    for ln in body.split('\n'):
        if ln.startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r'^(#{2,3})\s+(.*)$', ln)
        if m:
            depth = len(m.group(1)) - 2
            title = re.sub(r'[*`]', '', m.group(2)).strip()
            if depth == 0:
                lines.append('- [%s](#%s)' % (title, slug(m.group(2))))
    return '\n'.join(lines)


def main(src, dst):
    raw = open(src, encoding='utf-8').read()
    t = Tree()
    t.feed(raw)
    secs = t.root.find('section')
    parts = [render(s, 2) for s in secs]
    body = '\n\n'.join(p for p in parts if p.strip())
    body = re.sub(r'\n{3,}', '\n\n', body)

    # In-page links target HTML ids, which do not survive into Markdown.
    # Remap each to the slug of that section's own heading.
    anchors = {}
    for sec, md in zip(secs, parts):
        sid = sec.attrs.get('id')
        h = re.search(r'^##\s+(.*)$', md, re.M)
        if sid and h:
            anchors[sid] = slug(h.group(1))
    for sid, target in anchors.items():
        body = body.replace('](#%s)' % sid, '](#%s)' % target)
    left = set(re.findall(r'\]\(#([\w-]+)\)', body)) - set(anchors.values())
    if left:
        print('WARNING unresolved anchors:', sorted(left), file=sys.stderr)
    doc = '\n\n'.join(x for x in [
        masthead(t.root),
        '## Contents\n\n' + toc(body),
        body,
    ] if x.strip())
    doc = re.sub(r'\n{3,}', '\n\n', doc)
    open(dst, 'w', encoding='utf-8').write(doc + '\n')
    print('wrote', dst, len(doc), 'chars,', doc.count('\n') + 1, 'lines')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
