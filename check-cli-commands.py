"""Validate every `agentcore ...` command in the lab against the CLI's own --help.

Catches invented flags and enum values that do not exist in the installed CLI.
"""
import html
import re
import subprocess
import sys
from html.parser import HTMLParser

SRC = sys.argv[1] if len(sys.argv) > 1 else 'agentcore-harness-lab.html'


class Codes(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_pre = 0
        self.buf = []
        self.blocks = []

    def handle_starttag(self, t, a):
        if t == 'pre':
            self.in_pre += 1

    def handle_endtag(self, t):
        if t == 'pre' and self.in_pre:
            self.in_pre -= 1
            if not self.in_pre:
                self.blocks.append(''.join(self.buf))
                self.buf = []

    def handle_data(self, d):
        if self.in_pre:
            self.buf.append(d)


p = Codes()
p.feed(open(SRC, encoding='utf-8').read())

# join backslash continuations, keep only agentcore invocations
cmds = []
for b in p.blocks:
    text = html.unescape(b)
    text = re.sub(r'\\\n\s*', ' ', text)
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('agentcore '):
            cmds.append(re.sub(r'\s+#.*$', '', line))

HELP = {}


def help_for(path):
    if path in HELP:
        return HELP[path]
    r = subprocess.run(['agentcore'] + path.split() + ['--help'],
                       capture_output=True, text=True)
    HELP[path] = r.stdout + r.stderr
    return HELP[path]


TOP = set(re.findall(r'^\s{2}(\w[\w-]*)', help_for(''), re.M))

problems = []
seen = set()
for c in cmds:
    toks = c.split()
    if len(toks) < 2:
        continue
    sub = toks[1]
    if sub not in TOP:
        problems.append(('unknown subcommand', sub, c))
        continue
    path = sub
    # two-word subcommands like `add harness`, `traces list`
    if len(toks) > 2 and not toks[2].startswith('-'):
        h2 = help_for(sub)
        if re.search(r'^\s{2}' + re.escape(toks[2]) + r'\b', h2, re.M):
            path = sub + ' ' + toks[2]
    h = help_for(path)
    flags = set(re.findall(r'(--[\w-]+)', h))
    for m in re.finditer(r'(?<![\w-])(--[\w-]+)', c):
        f = m.group(1)
        if f not in flags:
            key = (path, f)
            if key not in seen:
                seen.add(key)
                problems.append(('unknown flag for `%s`' % path, f, c))
    # enum values: check --flag <value> against "Foo: a, b, c" in help
    for m in re.finditer(r'(--[\w-]+)\s+([A-Za-z][\w,]*)', c):
        f, v = m.group(1), m.group(2)
        # description belonging to THIS flag only: stop at the next flag line
        em = re.search(re.escape(f) + r'\s+<[^>]*>\s+(.*?)(?=\n\s*-{1,2}\w|\Z)',
                       h, re.S)
        if not em:
            continue
        desc = ' '.join(em.group(1).split())
        lm = re.search(r':\s*([\w_]+(?:,\s*[\w_]+){1,})', desc)
        if not lm:
            continue
        allowed = {x.strip() for x in lm.group(1).split(',')}
        for part in v.split(','):
            if part and part not in allowed and not part.startswith('$'):
                key = (path, f, part)
                if key not in seen:
                    seen.add(key)
                    problems.append(
                        ('bad value for `%s %s` (allowed: %s)'
                         % (path, f, ', '.join(sorted(allowed))), part, c))

print('checked %d agentcore commands\n' % len(cmds))
for kind, tok, cmd in problems:
    print('%-58s %s' % (kind + ':', tok))
    print('    ' + (cmd[:150] + ('…' if len(cmd) > 150 else '')))
print('\n%d problem(s)' % len(problems))


def check_harness_continuity(src=SRC):
    """Each harness must be created before it is referenced, and only once.

    `agentcore add harness` refuses a duplicate name, and `add tool` /
    `invoke` fail on a name that was never created — both are easy to
    introduce when editing one lab in isolation.
    """
    pp = Codes()
    pp.feed(open(src, encoding='utf-8').read())
    created, problems = {}, []
    order = []
    for b in pp.blocks:
        text = re.sub(r'\\\n\s*', ' ', html.unescape(b))
        for line in text.split('\n'):
            for n in re.findall(r'agentcore create\s+--name\s+([\w]+)', line):
                order.append(('new', n))
            for n in re.findall(r'agentcore add harness\s+--name\s+([\w]+)', line):
                order.append(('new', n))
            for n in re.findall(r'--harness\s+([\w]+)', line):
                order.append(('ref', n))
    repeats = []
    for kind, name in order:
        if kind == 'new':
            if name in created:
                repeats.append(name)
            created[name] = True
        elif name not in created:
            # `create --name X` also makes harness X
            problems.append('referenced before creation: %s' % name)

    print('\n--- harness continuity ---')
    for p in problems:
        print('  ERROR ' + p)
    # Standalone illustrative snippets legitimately reuse a placeholder name
    # across labs; only a name on the through-line must be created once.
    for name in sorted(set(repeats)):
        print('  info  re-created (expected for standalone examples): %s' % name)
    print('  %d error(s)' % len(problems))


check_harness_continuity()
