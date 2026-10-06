"""Document-local, explicit term decisions; never mutate existing translations."""
import re
from research import protect, restore, load


def word_pattern(term):
    return re.compile((r'(?<!\w)' if term[0].isalnum() else '') + re.escape(term)
                      + (r'(?!\w)' if term[-1].isalnum() else ''))


def applicable(rules, page):
    selected = {}
    for rule in sorted(rules, key=lambda r: r['page']):
        if rule['page'] in (0, page):
            selected[rule['term']] = rule
    return selected


def candidates(job, page, rules):
    found = {}
    pages = list((job / 'pages').glob('*/page.json'))
    for path in sorted(pages):
        number = int(path.parent.name)
        active = applicable(rules, number)
        for block in load(path).get('blocks', []):
            if block.get('visual'):
                continue
            source = block.get('source', '')
            _, mapping = protect(source)
            for term in dict.fromkeys(mapping.values()):
                if re.fullmatch(r'[\d.,]+', term):
                    continue
                if term in active:
                    continue
                symbolic = len(term) == 1 or bool(re.search(r'[$\\=<>α-ωΑ-Ω]', term))
                scope = number if symbolic else 0
                key = (term, scope)
                if key not in found:
                    found[key] = dict(term=term, page=scope, source=source, seenPage=number,
                                      reason='符号或公式候选' if symbolic else '全大写缩写候选')
    return list(found.values())


def prepare(source, rules, page, legacy_terms=()):
    active = applicable(rules, page)
    automatic = re.findall(r'\b[A-Z][A-Z0-9-]{1,15}\b', source)
    excluded = {'THE', 'AND', 'OF', 'IN', 'TO', 'FOR', 'WITH', 'ON', 'BY', 'AS', 'IS', 'AN'}
    for term in list(legacy_terms) + [t for t in automatic if t not in excluded]:
        term = term.strip()
        if term:
            active.setdefault(term, dict(term=term, mode='keep', target='', page=0))
    # Hide explicit decisions from automatic formula/variable detection first.
    selected = {}
    if active:
        pattern = re.compile('|'.join(word_pattern(t).pattern for t in sorted(active, key=len, reverse=True)))
        def replace(match):
            token = f'zzdecisiontoken{len(selected)}zz'
            selected[token] = active[match.group()]
            return token
        source = pattern.sub(replace, source)
    protected, mapping = protect(source, (), protect_uppercase=False)
    # A whole formula wins over decisions for individual words inside it.
    for marker, formula in mapping.items():
        for token, rule in selected.items():
            formula = formula.replace(token, rule['term'])
        mapping[marker] = formula
    checks = []
    for token, rule in selected.items():
        if token not in protected:
            continue
        term, mode = rule['term'], rule['mode']
        if mode == 'keep' and (len(term) == 1 or re.search(r'[$\\=<>α-ωΑ-Ω]', term)):
            marker = f'[[KEEP{len(mapping):04d}]]'
            mapping[marker] = term
            protected = protected.replace(token, marker)
        else:
            protected = protected.replace(token, term)
            if mode != 'translate':
                checks.append((term, rule['target'] if mode == 'fixed' else term))
    instructions = '\n'.join(f'术语 {term} 必须使用：{target}' for term, target in dict.fromkeys(checks))
    return protected, mapping, checks, instructions


def validate(raw, mapping, checks):
    result = restore(raw, mapping)
    for term, target in dict.fromkeys(checks):
        count = sum(t == target for _, t in checks)
        actual = len(re.findall(re.escape(target), result)) if not target.isascii() else len(
            re.findall(r'(?<![A-Za-z0-9_])' + re.escape(target) + r'(?![A-Za-z0-9_])', result))
        if actual < count:
            raise ValueError(f'术语校验未通过：{term} 应保留为 {target}（需要 {count} 处，返回 {actual} 处）')
    return result
