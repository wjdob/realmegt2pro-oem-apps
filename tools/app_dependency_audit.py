"""Compare app instruction references with two explicit framework inventories.

Inputs are AppDexInventory TSV files. This is a structural triage tool. Its result
does not establish classloader visibility, reachability, hidden-API access or ABI.
"""
from pathlib import Path
import argparse,hashlib,json

def read_inventory(paths):
    classes={};methods=set();fields=set();references=[];origins={}
    for path in paths:
        for line in path.read_text(encoding='utf-8').splitlines():
            row=line.split('\t');kind=row[0]
            if kind=='class':
                if len(row)!=4:raise ValueError('invalid class inventory row')
                parents=tuple(x for x in [row[2],*row[3].split(',')] if x)
                if row[1] in classes and classes[row[1]]!=parents:
                    raise ValueError('conflicting hierarchy; qualify classloader providers first: '+row[1])
                classes[row[1]]=parents;origins.setdefault(row[1],set()).add(str(path))
            elif kind in ('method','field'):
                if len(row)!=2 or '->' not in row[1]:raise ValueError('invalid member row')
                (methods if kind=='method' else fields).add(row[1])
            elif kind in ('call','fieldref','typeuse'):
                if len(row)!=3:raise ValueError('invalid instruction reference row')
                references.append(tuple(row))
    return dict(classes=classes,methods=methods,fields=fields,references=references,origins=origins)

def resolve(symbol,kind,index):
    owner,member=symbol.split('->',1);pending=[owner];seen=set()
    while pending:
        current=pending.pop()
        if current in seen:continue
        seen.add(current)
        if len(index['origins'].get(current,()))>1:
            return None  # All JARs together are not a real classloader.
        if current+'->'+member in index['methods' if kind=='call' else 'fields']:return True
        pending.extend(index['classes'].get(current,()))
    return False

def compare(app,before,after):
    removed=[];ambiguous=[]
    for kind,symbol,caller in app['references']:
        if kind not in ('call','fieldref'):continue
        owner=symbol.split('->',1)[0]
        # Embedded app/declared shared-library definitions require their own
        # classloader analysis, not an unrelated framework JAR of the same name.
        if owner in app['classes'] or owner not in before['classes'] or owner not in after['classes']:continue
        first=resolve(symbol,kind,before);second=resolve(symbol,kind,after)
        row=dict(kind=kind,symbol=symbol,caller=caller)
        if first is None or second is None:ambiguous.append(row)
        elif first and not second:removed.append(row)
    return {'removed_members_referenced':removed,'ambiguous_provider_references':ambiguous,
            'limits':'Named framework inventories and static instruction references only. Missing classes, actual boot/shared classpaths, call reachability, catch handlers, APEX, reflection, hidden-API flags and JNI/native ABI remain separate gates.'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app',type=Path,nargs='+',required=True)
    p.add_argument('--before',type=Path,nargs='+',required=True)
    p.add_argument('--after',type=Path,nargs='+',required=True)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise ValueError('new output path required')
    report=compare(read_inventory(a.app),read_inventory(a.before),read_inventory(a.after))
    report['inputs']={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in a.app+a.before+a.after}
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:len(report[k]) for k in ('removed_members_referenced','ambiguous_provider_references')}))

if __name__=='__main__':main()
