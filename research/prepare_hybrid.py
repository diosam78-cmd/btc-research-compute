"""Apply a narrow, reproducible hybrid-entry patch to audited Stage-1 engine.

First tranche fills at confirmed breakout close. Remaining 50% or 25% (step-rounded)
waits for 0.25N/0.50N retracement from favorable CLOSE extreme; 24/72-hour
expiry or break back through frozen breakout channel cancels the remainder.
While remainder pending, no pyramid ADD is allowed. After cancellation the
original full unit remains the ADD size; first fill determines ADD trigger.
Exit has priority, and a remainder fill consumes the bar's one event.
"""
from pathlib import Path
p=Path('research/pullback_hybrid.py')
s=p.read_text(encoding='utf-8')
def replace_once(old,new):
    global s
    n=s.count(old)
    if n!=1: raise ValueError(f'Expected one match, got {n}: {old[:90]!r}')
    s=s.replace(old,new,1)
replace_once("VARIANTS = {\n    'BASE':      dict(entry_pb=None, add_pb=None),\n    'E25':       dict(entry_pb=0.25, add_pb=None),\n    'E50':       dict(entry_pb=0.50, add_pb=None),\n    'A25':       dict(entry_pb=None, add_pb=0.25),\n    'A50':       dict(entry_pb=None, add_pb=0.50),\n    'EA25':      dict(entry_pb=0.25, add_pb=0.25),\n    'EA50':      dict(entry_pb=0.50, add_pb=0.50),\n    'E25_A50':   dict(entry_pb=0.25, add_pb=0.50),\n    'E50_A25':   dict(entry_pb=0.50, add_pb=0.25),\n}","VARIANTS = {\n    'BASE': dict(entry_pb=None, add_pb=None, hybrid=None),\n    'H50_P25_24H': dict(entry_pb=None, add_pb=None, hybrid=dict(first=0.50, depth=0.25, expiry=24)),\n    'H50_P50_24H': dict(entry_pb=None, add_pb=None, hybrid=dict(first=0.50, depth=0.50, expiry=24)),\n    'H50_P25_72H': dict(entry_pb=None, add_pb=None, hybrid=dict(first=0.50, depth=0.25, expiry=72)),\n    'H50_P50_72H': dict(entry_pb=None, add_pb=None, hybrid=dict(first=0.50, depth=0.50, expiry=72)),\n    'H75_P25_24H': dict(entry_pb=None, add_pb=None, hybrid=dict(first=0.75, depth=0.25, expiry=24)),\n}")
replace_once("OUT = Path('results_pullback_stage1')","OUT = Path('results_hybrid_entry')")
replace_once("    add_arm = None    # dict(extreme,ref_fill,time)\n", "    add_arm = None    # dict(extreme,ref_fill,time)\n    hybrid_pending = None # dict(dir, extreme, level, n, remainder, armed_at, expiry)\n    hybrid_armed=hybrid_filled=hybrid_cancel=hybrid_expired=hybrid_fallback=hybrid_blocked_add=0\n")
replace_once("                entry_arm = None; add_arm = None\n                event = True", "                entry_arm = None; add_arm = None\n                if hybrid_pending is not None:\n                    hybrid_pending = None; hybrid_cancel += 1\n                event = True")
needle="        # 2) ADD or ADD-ARM / pullback execution.\n        if side and not event and units < p['maxu']:"
new="""        # HYBRID remainder: execute only on a LATER confirmed hourly close.
        # Original first tranche's stop and future ADD trigger remain anchored to first fill.
        if side and not event and hybrid_pending is not None:
            hp=hybrid_pending
            if (px <= hp['level'] if side > 0 else px >= hp['level']):
                hybrid_pending=None; hybrid_cancel+=1
            elif t - hp['armed_at'] >= pd.Timedelta(hours=hp['expiry']):
                hybrid_pending=None; hybrid_expired+=1
            else:
                hp['extreme']=(max(hp['extreme'],px) if side>0 else min(hp['extreme'],px))
                pullback_hit=(px <= hp['extreme']-hp['depth']*hp['n'] if side>0
                              else px >= hp['extreme']+hp['depth']*hp['n'])
                if pullback_hit:
                    fp=fill_price(px,side)
                    eq=cash+side*qty*(px-avg)
                    room=max(0.0,(eq*CAP-qty*px)/(1.0+CAP*FEE))
                    rem=hp['remainder']
                    if valid_qty(rem,fp,p) and rem*fp <= room and eq>0:
                        fee=rem*fp*FEE
                        cash-=fee; fees_in_pos+=fee
                        avg=(avg*qty+fp*rem)/(qty+rem)
                        qty+=rem
                        hybrid_pending=None; hybrid_filled+=1
                        event=True # one fill event this hour; no ADD on same bar
                    else:
                        # Do not model an impossible second tranche as filled.
                        hybrid_pending=None; hybrid_cancel+=1; rejected+=1

        # 2) ADD or ADD-ARM / pullback execution.
        if side and not event and hybrid_pending is not None:
            hybrid_blocked_add += 1
        if side and not event and hybrid_pending is None and units < p['maxu']:"""
replace_once(needle,new)
needle="""                        if valid_qty(q, fp, p) and cash > 0:
                            fee = q * fp * FEE; cash -= fee; fees_in_pos = fee
                            side = d; qty = q; avg = fp; unit = q; entry_n = bar.n; last_fill = fp
                            stop = fp - d * p['stop'] * bar.n; units = 1; entry_time = t
                            entry_exec += 1; event = True
                        else:
                            rejected += 1
                    else:
                        entry_arm = {"""
new="""                        if valid_qty(q, fp, p) and cash > 0:
                            actual_first=q
                            hcfg=variant.get('hybrid')
                            if hcfg is not None:
                                q_first=floor_step(q*hcfg['first'],p['step'])
                                q_rem=round(q-q_first, 10)
                                if valid_qty(q_first,fp,p) and valid_qty(q_rem,fp,p):
                                    actual_first=q_first
                                    hybrid_pending={'dir':d,'extreme':px,'level':float(bar.eh if d>0 else bar.el),
                                                   'n':float(bar.n),'remainder':q_rem,
                                                   'armed_at':t,'depth':hcfg['depth'],'expiry':hcfg['expiry']}
                                    hybrid_armed+=1
                                else:
                                    hybrid_fallback+=1 # exchange filter makes splitting impossible: full baseline fill
                            fee = actual_first * fp * FEE; cash -= fee; fees_in_pos = fee
                            side = d; qty = actual_first; avg = fp; unit = q; entry_n = bar.n; last_fill = fp
                            stop = fp - d * p['stop'] * bar.n; units = 1; entry_time = t
                            entry_exec += 1; event = True
                        else:
                            rejected += 1
                    else:
                        entry_arm = {"""
replace_once(needle,new)
replace_once("        'year_returns_json': json.dumps(years_ret, sort_keys=True),", "        'year_returns_json': json.dumps(years_ret, sort_keys=True),\n        'hybrid_armed':hybrid_armed,'hybrid_filled':hybrid_filled,\n        'hybrid_cancel':hybrid_cancel,'hybrid_expired':hybrid_expired,\n        'hybrid_fallback':hybrid_fallback,'hybrid_blocked_add_bars':hybrid_blocked_add,")
replace_once("'pullback_rule': 'Arm on normal breakout/add trigger. Track favorable CLOSE extreme. Execute full unit only after specified N retracement. Entry setup cancels if close returns through frozen breakout level. Add setup cancels if close returns through prior fill. No timeout. No partial fills in stage 1.'", "'pullback_rule': 'HYBRID: first tranche at breakout close, remainder waits for retracement from favorable close extreme, cancels on breakout reversal or expires after 24/72h; while pending, no ADD. Strict one event per bar. Base unchanged.'")
p.write_text(s,encoding='utf-8')
print('Hybrid patch applied; variants:',s[s.index('VARIANTS = {'):s.index('\n}\n\nSTART')+2]);print('hybrid pending logic verified:',s.count('hybrid_pending'))
