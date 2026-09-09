#!/usr/bin/env python3
"""Apply the figurative-motion catalogue to ch/*.json (idempotent).

Every entry here is a chapter's own image, traced to a sentence in that
chapter: the bone that becomes a vessel, the watchers who become travellers,
fire, doors, clocks, gates. The engine draws them; this file says where and
when. The catalogue with its reasons is [[vinc.plan.book-figurative-motion-v2]].

  python -X utf8 figures.py        patch every chapter in place
  python -X utf8 figures.py --dry  report only
"""
import json, glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
GROUND = [320, 612, 250, 18]

# station name -> the small figure drawn under it (first match wins)
STATION_GLYPH = [
    ('document', 'doc'), ('source', 'doc'), ('sentence', 'doc'), ('question', 'question'),
    ('chunk', 'stack'), ('kind', 'book'), ('concept', 'book'), ('named', 'tablet'),
    ('edge', 'arrow'), ('directed', 'arrow'), ('relationship', 'arrow'), ('mentions', 'arrow'),
    ('time', 'clock'), ('clock', 'clock'), ('space', 'logo'), ('graph', 'logo'),
    ('agent', 'agent'), ('role', 'agent'), ('tool', 'gate'), ('gate', 'gate'), ('auth', 'gate'),
    ('grade', 'gate'), ('evidence', 'doc'), ('record', 'doc'), ('resolve', 'check'),
    ('answer', 'check'), ('confirmed', 'check'), ('compared', 'check'),
    ('vector', 'cube'), ('embed', 'cube'), ('model', 'cube'), ('dimension', 'cube'),
    ('channel', 'door'), ('entry', 'door'), ('door', 'door'), ('reach', 'door'),
    ('view', 'screen'), ('screen', 'screen'), ('render', 'screen'), ('layout', 'screen'),
    ('material', 'stack'), ('brief', 'stack'), ('result', 'stack'), ('candidate', 'stack'),
    ('pooled', 'stack'), ('excerpt', 'stack'), ('reading', 'stack'), ('fragment', 'doc'),
    ('name', 'tablet'), ('title', 'tablet'), ('mission', 'question'),
    ('neighbour', 'cone'), ('density', 'cone'), ('causal', 'cone'), ('cone', 'cone'),
    ('person', 'astronaut'), ('human', 'astronaut'), ('owner', 'astronaut'),
    ('team', 'logo'), ('personal', 'logo'), ('member', 'astronaut'),
]


def station_glyph(name):
    n = name.lower()
    for key, g in STATION_GLYPH:
        if key in n:
            return g
    return None


def fig(id, glyph, p, size, at, **kw):
    d = {'id': id, 'glyph': glyph, 'p': p, 'size': size, 'at': at}
    d.update(kw)
    return d


def mark(ch, id, **kw):
    for m in ch['marks'] + ch.get('anchors', []):
        if m['id'] == id:
            m.update(kw)
            return m
    raise KeyError(f"{ch['n']}: no mark {id}")


def hub(cx, cy, r, spoke_r, center, spokes, **kw):
    d = {'cx': cx, 'cy': cy, 'r': r, 'spokeR': spoke_r, 'center': center, 'spokes': spokes}
    d.update(kw)
    return d


def spoke(name, glyph, at, **kw):
    d = {'name': name, 'glyph': glyph, 'at': at}
    d.update(kw)
    return d


# ---------------------------------------------------------------- Part 0

def ch01(c):
    # OPEN, the thrown object: a bone rises from the ground, tumbles, and at
    # the top of its arc the same four strokes are a vessel. Scrubbed on the
    # opening beat; it parks in the top-left corner and recedes at beat 3.
    # Beat 1, the ring: nine watchers around the mark, and the mark IS the
    # discovery. At the close (focus beat) the same nine become travellers.
    mark(c, 'c0', r=44)
    for i in range(9):
        m = mark(c, f'p{i}', glyph='ape', morphGlyph='astronaut', gs=28)
        m.pop('morphToHex', None)
    mark(c, 'g', glyph='agent', gs=36, z=0.95)
    mark(c, 'q', z=0.8)
    c['figures'] = [
        fig('thrown', 'bone', [96, 84], 54, 0, rot=15, dim=3,
            fly={'p0': [300, 596], 'rot0': -35, 'arc': 40, 'size0': 62, 'from': 0.05, 'to': 0.85},
            morph={'glyph': 'ship', 'at': 0, 'from': 0.55, 'to': 0.9}),
        fig('core', 'logo', [220, 340], 80, 1, wash='peri', washAt=8, washR=54),
        fig('fire', 'fire', [318, 552], 38, 2, dim=3, wash='danger', washR=22, washFrom=0.3),
        fig('tablet', 'tablet', [430, 566], 28, 7, ink=2, double=False),
        fig('book', 'book', [490, 570], 30, 7, ink=2, double=False),
        fig('screen', 'screen', [550, 566], 30, 7, ink=2, double=False),
    ]
    c['ground'] = [320, 604, 246, 24]
    c['stage'] = []


def ch02(c):
    mark(c, 'core', glyph='logo', gs=58, z=0.9, wash='peri', washAt=6, washR=40)
    mark(c, 'd1', glyph='agent', gs=30)
    mark(c, 'd3', glyph='agent', gs=30)
    for w in ('w1', 'w2', 'w3'):
        mark(c, w, glyph='door', gs=24, z=0.35)
    mark(c, 'lone', z=0.3)
    c['figures'] = [
        fig('lens', 'lens', [214, 222], 28, 3, dim=4),
    ]
    c['ground'] = [320, 600, 246, 24]


def ch03(c):
    c['figures'] = [
        fig('asks', 'agent', [96, 118], 40, 1, dim=4, label='a general question', labelDy=16),
        fig('ours', 'logo', [560, 84], 40, 3, wash='peri', washAt=4, washR=28, label='our memory', labelDy=14),
    ]
    c['ground'] = [320, 600, 246, 20]


def ch04(c):
    mark(c, 'K', z=0.9)
    for t in ('t1', 't2', 't3'):
        mark(c, t, z=0.5)
    for d in ('d1', 'd2'):
        mark(c, d, z=0.3)
    c['figures'] = [
        # the sweep: a lens passes down through the undifferentiated mass and
        # comes back with nothing; the red scribble is the nothing
        fig('sweep', 'lens', [176, 410], 36, 1, until=1, wash='danger', washR=22, washFrom=0.72, washTo=0.95,
            fly={'p0': [176, 186], 'from': 0.2, 'to': 0.9}),
    ]
    c['ground'] = GROUND


def ch05(c):
    # three hands: a person, an assistant, an agent, each recording the event
    mark(c, 'bad', wash='danger', washAt=6, washR=22)
    mark(c, 'ok', wash='ok', washAt=7, washR=22)
    mark(c, 'who', glyph='astronaut', gs=34)
    c['figures'] = [
        fig('w0', 'astronaut', [150, 70], 34, 0, dim=2, double=False),
        fig('w1', 'agent', [320, 46], 34, 0, dim=2, double=False),
        fig('w2', 'agent', [490, 70], 34, 0, dim=2, double=False),
    ]
    c['ground'] = [320, 628, 250, 16]


def ch06(c):
    mark(c, 'n3', wash='peri', washAt=2, washR=22)
    c['figures'] = [
        fig('wt', 'clock', [96, 240], 44, 4, until=5, label='world time', labelDy=14),
        fig('gt', 'clock', [566, 240], 44, 4, until=5, label='graph time', labelDy=14, rot=90),
    ]
    c['ground'] = [320, 612, 250, 18]


def ch07(c):
    mark(c, 'g0', glyph='logo', gs=34)
    mark(c, 'A', glyph='agent', gs=40, z=0.5)
    mark(c, 'B', glyph='agent', gs=40, z=0.95, wash='peri', washAt=9, washR=30)
    for r in ('D', 'I', 'V'):
        mark(c, r, glyph='agent', gs=26, z=0.3)
    mark(c, 'f', wash='danger', washAt=7, washR=18)
    c['figures'] = []
    c['ground'] = GROUND


def ch08(c):
    mark(c, 's3', glyph='agent', gs=34, z=0.9)
    mark(c, 'ag', glyph='agent', gs=30)
    mark(c, 'ex', wash='ok', washAt=6, washR=14)
    mark(c, 'cl', z=0.85)
    c['figures'] = [
        fig('wrong', 'cross', [116, 96], 26, 1, until=1, wash='danger', washR=20, washFrom=0.05, washTo=0.5, double=False),
        fig('age', 'clock', [440, 188], 36, 3, until=3),
    ]
    # the stage drew its own cross on the source; the figure carries it now
    st = '\n'.join(c['stage'])
    old = ("      if (s === 1) {\n"
           "        // the source is marked wrong: a cross through it, one ink, one weight\n"
           "        kids.push(E('g', { key: 'wrong' },\n"
           "          E('line', { x1: 104, y1: 84, x2: 128, y2: 108, stroke: INK, strokeWidth: 1, strokeLinecap: 'round' }),\n"
           "          E('line', { x1: 128, y1: 84, x2: 104, y2: 108, stroke: INK, strokeWidth: 1, strokeLinecap: 'round' })));\n"
           "      }\n")
    if old in st:
        c['stage'] = st.replace(old, '').split('\n')
    c['ground'] = GROUND


def ch09(c):
    mark(c, 'hub', r=44)
    for i in range(9):
        m = mark(c, f'p{i}', glyph='astronaut', gs=28)
        m.pop('morphToHex', None)
    mark(c, 'blank', wash='warn', washAt=3, washR=24, z=0.3)
    st = '\n'.join(c['stage'])
    old = ("      kids.push(E('path', { key: 'hub', d: blob(220, 340, 20, lerp(K, 0, t)),\n"
           "        fill: mixHex(INK, FOCUS, t) }));\n")
    new = ("      // the mark itself returns, drawn as the mark, with the one wash of\n"
           "      // the chapter scribbled in under it\n"
           "      kids.push(washPatch(220, 340, 54, FOCUS, 9, t, 'hubwash'));\n"
           "      kids.push(...glyphEls('logo', null, 0, 220, 340, 80, 0, INK, 9, 'hub', { double: true }));\n")
    if old in st:
        st = st.replace(old, new)
    c['stage'] = st.split('\n')
    c['figures'] = []
    c['ground'] = [320, 630, 250, 14]


# ---------------------------------------------------------------- Part 1

def stations(c):
    """small figures under each station name, and a document as the payload
    when a document is what enters the belt"""
    cv = c.get('conveyor')
    if not cv:
        return
    for i, st in enumerate(cv['stations']):
        g = station_glyph(st['name'])
        if g:
            st['glyph'] = g
        else:
            st.pop('glyph', None)
        if i == 0 and st.get('shape') == 'hex' and st.get('count', 1) == 1:
            st['payload'] = 'doc'
        else:
            st.pop('payload', None)


def ch10(c):
    # the archetypal hub: one memory, six kinds around it, each named on its
    # own spoke. Replaces the conveyor: this chapter's argument is "one
    # thing, six faces", not a pipeline.
    c.pop('conveyor', None)
    c['hub'] = hub(320, 300, 70, 196,
        {'glyph': 'logo', 'size': 76, 'label': 'one memory', 'wash': 'peri', 'washAt': 5},
        [spoke('Concept', 'book', 1, sub='what this means'),
         spoke('Episode', 'clock', 1, sub='when it happened'),
         spoke('Record', 'doc', 1, sub='what was observed'),
         spoke('Decision', 'gate', 1, sub='what was chosen'),
         spoke('Bridge', 'arrow', 1, sub='what joins areas'),
         spoke('Entity', 'tablet', 1, sub='which thing')],
        at=0, ringAt=0, note='an edge carries a kind and a direction', noteAt=3)
    c['figures'] = [
        fig('src', 'doc', [562, 548], 40, 5, label='kept beside it', labelDy=16),
    ]


def ch11(c):
    c.pop('conveyor', None)
    c['hub'] = hub(320, 300, 64, 190,
        {'glyph': 'doc', 'size': 64, 'label': 'one sentence'},
        [spoke('Concept', 'book', 2, sub='what it means'),
         spoke('prerequisite of', 'arrow', 3, dir='out'),
         spoke('Episode', 'clock', 2, sub='when it happened'),
         spoke('derives from', 'arrow', 3, dir='in'),
         spoke('Record', 'doc', 2, sub='what was observed'),
         spoke('about', 'arrow', 3, dir='in')],
        at=0, ringAt=1, crossAt=5, note='everything connected to everything', noteAt=5)
    c['figures'] = []


def ch12(c):
    stations(c)
    c['figures'] = [
        fig('three', 'stack', [104, 118], 42, 1, label='three appearances', labelDy=14),
        fig('one', 'logo', [536, 116], 42, 3, wash='peri', washAt=3, washR=30, label='one memory', labelDy=14),
    ]


def ch13(c):
    stations(c)
    c['figures'] = [
        fig('kinds', 'tablet', [104, 116], 38, 1, label='kinds: closed', labelDy=14),
        fig('concepts', 'book', [536, 116], 40, 1, label='concepts: open', labelDy=14),
        fig('title', 'lens', [320, 132], 34, 5, wash='warn', washR=24, label='a title is not understanding', labelDy=14),
    ]


def ch14(c):
    stations(c)
    c['figures'] = [
        fig('unknown', 'question', [320, 128], 40, 3, wash='warn', washR=26, label='visibly unknown', labelDy=14),
    ]


def ch15(c):
    stations(c)
    c['figures'] = [
        fig('kept', 'doc', [104, 118], 40, 1, label='preserved first', labelDy=14),
        fig('found', 'lens', [320, 126], 34, 2, label='extraction proposes', labelDy=14),
        fig('refined', 'astronaut', [536, 112], 38, 4, label='a person refines', labelDy=14),
    ]


def ch16(c):
    c.pop('conveyor', None)
    c['hub'] = hub(320, 300, 62, 190,
        {'glyph': 'lens', 'size': 60, 'label': 'the question'},
        [spoke('full text', 'doc', 1, sub='matching wording'),
         spoke('vector', 'cube', 1, sub='semantic proximity'),
         spoke('graph walk', 'logo', 1, sub='one hop at a time'),
         spoke('candidates', 'stack', 2),
         spoke('held', 'question', 2, sub='two fit equally', wash='warn'),
         spoke('entry point', 'door', 5)],
        at=0, ringAt=1, note='search narrows; something else decides', noteAt=4)
    c['figures'] = []


def ch17(c):
    c['figures'] = [
        fig('model', 'cube', [560, 112], 40, 2, wash='peri', washAt=5, washR=28, label='the model', labelDy=14),
    ]


def ch18(c):
    c.pop('conveyor', None)
    c['hub'] = hub(320, 300, 60, 190,
        {'glyph': 'check', 'size': 58, 'label': 'resolved', 'wash': 'ok', 'washAt': 2},
        [spoke('full text', 'doc', 1, dir='in'),
         spoke('vector', 'cube', 1, dir='in'),
         spoke('graph', 'logo', 1, dir='in'),
         spoke('material', 'stack', 3),
         spoke('record', 'doc', 4, sub='not a concept'),
         spoke('no choice', 'question', 5, sub='a normal answer')],
        at=0, ringAt=1)
    c['figures'] = []


def ch19(c):
    stations(c)
    c['figures'] = [
        fig('stack', 'stack', [320, 130], 46, 3, wash='peri', washR=30, label='a stack of excerpts, not a sentence', labelDy=16),
    ]


def ch20(c):
    c.pop('conveyor', None)
    c['hub'] = hub(320, 300, 62, 190,
        {'glyph': 'door', 'size': 62, 'label': 'the relay'},
        [spoke('agent', 'agent', 1, dir='in'),
         spoke('local channel', 'screen', 2, dir='both'),
         spoke('remote channel', 'arrow', 2, dir='both'),
         spoke('safety class', 'gate', 3, sub='unsure means blocked'),
         spoke('auth + space', 'check', 4, sub='both, before the tool runs'),
         spoke('budgeted result', 'stack', 5, sub='and it says it shrank')],
        at=0, ringAt=1, note='a door is not the room', noteAt=0)
    c['figures'] = []


def ch21(c):
    stations(c)
    c['figures'] = [
        fig('agent', 'agent', [104, 116], 40, 1, label='an agent', labelDy=14),
        fig('node', 'logo', [536, 116], 40, 1, label='a node', labelDy=14),
        fig('sched', 'clock', [320, 128], 38, 4, until=4),
        fig('nosched', 'cross', [320, 128], 30, 4, until=4, wash='danger', washR=24, washFrom=0.3, double=False,
            label='the graph is not a scheduler', labelDy=26),
    ]


def ch21b(c):
    c.pop('conveyor', None)
    c['hub'] = hub(320, 300, 62, 190,
        {'glyph': 'agent', 'size': 66, 'label': 'a role'},
        [spoke('mission', 'question', 1, sub='change, or only confirm'),
         spoke('reading order', 'stack', 2, sub='first, then, last'),
         spoke('gate', 'gate', 3, sub='pass, or stay'),
         spoke('flow', 'arrow', 4, sub='research · design · implement'),
         spoke('rendered', 'screen', 5),
         spoke('compared back', 'check', 5, wash='ok')],
        at=0, ringAt=1)
    c['figures'] = []


def ch22(c):
    stations(c)
    c['figures'] = [
        fig('ev', 'doc', [536, 116], 40, 1, label='evidence', labelDy=14),
        fig('conf', 'question', [430, 118], 34, 4, wash='warn', washR=22, label='confidence, separately', labelDy=14),
    ]


def ch23(c):
    c['figures'] = [
        fig('wt', 'clock', [506, 470], 36, 1, label='world', labelDy=12),
        fig('gt', 'clock', [150, 128], 36, 1, label='graph', labelDy=12, rot=90),
    ]


def ch24(c):
    stations(c)
    c['figures'] = [
        fig('view', 'screen', [320, 128], 44, 3, label='a view, not coordinates', labelDy=14),
    ]


def ch25(c):
    stations(c)
    c['figures'] = [
        fig('personal', 'logo', [190, 116], 40, 1, label='personal', labelDy=14),
        fig('team', 'logo', [450, 116], 40, 1, label='team', labelDy=14),
        fig('wall', 'wall', [320, 118], 56, 3, wash='danger', washR=22, label='owner isolation', labelDy=16),
    ]


def ch26(c):
    c['figures'] = [
        fig('clk', 'clock', [104, 108], 38, 1, until=2),
        fig('notcause', 'cross', [104, 108], 30, 1, until=2, wash='danger', washR=22, washFrom=0.4, double=False,
            label='order is not cause', labelDy=26),
    ]


def ch27(c):
    stations(c)
    c['figures'] = [
        fig('lens', 'lens', [320, 132], 34, 4, wash='warn', washR=24, label='density does not explain itself', labelDy=14),
    ]


# ---------------------------------------------------------------- Part 2

def ch28(c):
    c['figures'] = [
        fig('fixture', 'doc', [96, 92], 40, 1, label='the fixture', labelDy=14),
        fig('valid', 'check', [138, 76], 22, 1, wash='ok', washR=16, double=False),
    ]


def ch29(c):
    c['figures'] = [
        fig('agent', 'agent', [96, 92], 40, 1, label='pays in calls', labelDy=14),
    ]


def ch30(c):
    c['figures'] = [
        fig('a', 'cube', [230, 96], 34, 1, label='model A', labelDy=12),
        fig('fix', 'doc', [358, 96], 36, 1, label='one fixture', labelDy=12),
        fig('b', 'cube', [486, 96], 34, 1, label='model B', labelDy=12),
    ]


def ch31(c):
    c['figures'] = [
        fig('re', 'stack', [96, 92], 40, 2, label='re-embedded', labelDy=14),
        fig('clk', 'clock', [544, 92], 36, 2, label='about twenty-five seconds', labelDy=12),
    ]


def ch32(c):
    # the flagship of Part 2: a healthy bar, and behind it the fixture it
    # measured, drawn faint because it is empty. No wash: flatness is the point.
    c['figures'] = [
        fig('fixture', 'doc', [452, 92], 40, 1, ink=2, double=False, label='empty', labelAt=2, labelDy=14),
    ]


def ch33(c):
    c['figures'] = [
        fig('gate', 'gate', [320, 92], 44, 2, wash='ok', washR=28, label='the measurement gate', labelDy=14),
    ]


# ---------------------------------------------------------------- Part 3

def ch35(c):
    R = c['ring']
    R['glyphs'] = ['doc', 'logo', 'lens', 'agent', 'astronaut', 'check', 'gate', 'doc']
    R['traveller'] = 'astronaut'
    R['travellerWash'] = 'peri'
    c['figures'] = []


PATCH = {
    '01': ch01, '02': ch02, '03': ch03, '04': ch04, '05': ch05, '06': ch06, '07': ch07, '08': ch08, '09': ch09,
    '10': ch10, '11': ch11, '12': ch12, '13': ch13, '14': ch14, '15': ch15, '16': ch16, '17': ch17, '18': ch18,
    '19': ch19, '20': ch20, '21': ch21, '21b': ch21b, '22': ch22, '23': ch23, '24': ch24, '25': ch25, '26': ch26,
    '27': ch27, '28': ch28, '29': ch29, '30': ch30, '31': ch31, '32': ch32, '33': ch33, '35': ch35,
}


def main():
    dry = '--dry' in sys.argv
    n = 0
    for f in sorted(glob.glob(os.path.join(HERE, 'ch', '*.json'))):
        key = os.path.basename(f)[2:-5]
        fn = PATCH.get(key)
        if not fn:
            print(f'ch{key}: no entry'); continue
        c = json.load(open(f, encoding='utf-8'))
        fn(c)
        figs = len(c.get('figures', []))
        gl = sum(1 for m in c['marks'] if m.get('glyph'))
        wash = sum(1 for m in c['marks'] if m.get('wash')) + sum(1 for g in c.get('figures', []) if g.get('wash'))
        reg = 'hub' if c.get('hub') else ('conveyor' if c.get('conveyor') else ('pair' if c.get('pair') else ('ring' if c.get('ring') else 'canvas')))
        print(f'ch{key:4} {reg:8} figures={figs} glyph-marks={gl} washes={wash}')
        if not dry:
            with open(f, 'w', encoding='utf-8', newline='\n') as fh:
                json.dump(c, fh, ensure_ascii=False, indent=2)
                fh.write('\n')
        n += 1
    print(f'--- {n} chapters {"checked" if dry else "patched"}')


if __name__ == '__main__':
    main()
