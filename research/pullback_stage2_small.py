from pathlib import Path
import pullback_stage1 as b

vals = [0.05, 0.10, 0.15, 0.20, 0.25]
variants = {'BASE': dict(entry_pb=None, add_pb=None)}
for x in vals:
    tag = f'{int(round(x*100)):02d}'
    variants[f'E{tag}'] = dict(entry_pb=x, add_pb=None)
    variants[f'A{tag}'] = dict(entry_pb=None, add_pb=x)
    variants[f'EA{tag}'] = dict(entry_pb=x, add_pb=x)

b.VARIANTS = variants
b.OUT = Path('results_pullback_stage2_small')
b.OUT.mkdir(exist_ok=True)
b.main()
