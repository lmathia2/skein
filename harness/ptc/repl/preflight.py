"""Conservative straight-line helper checking; unknown Python stays runtime-checked."""
from __future__ import annotations

import ast
import difflib
import inspect


def helper_contract(broker_type: type) -> dict:
    schemas = {
        'read': dict(path='str', text='str', sha256='str', offset='int', returned_lines='int',
                     total_lines='int', complete='bool', next_offset=None),
        'write': dict(path='str', changed='bool', sha256='str', diff='str'),
        'edit': dict(path='str', changed='bool', sha256='str', diff='str'),
        'bash': dict(exit_code=None, stdout='str', stderr='str', output='str', timed_out='bool',
                     timeout_seconds='int', effects_unknown='bool'),
        'verify': dict(exit_code=None, stdout='str', stderr='str', output='str', timed_out='bool',
                       timeout_seconds='int', effects_unknown='bool'),
    }
    result = {}
    for name, schema in schemas.items():
        signature = inspect.signature(getattr(broker_type, name))
        signature = signature.replace(parameters=[p for p in signature.parameters.values() if p.name != 'self'])
        result[name] = {'signature': signature, 'result': {'status': 'str', 'data': schema}}
    return result


def validate(tree: ast.Module, namespace: dict, contract: dict) -> None:
    known = set(namespace) | set(namespace.get('__builtins__', {}))
    values = {name: type(value).__name__ for name, value in namespace.items()
              if type(value) in {str, int, float, bool, list, tuple, dict}}
    aliases = {'agent.fs.read': 'read', 'agent.fs.write': 'write',
               'agent.fs.edit': 'edit', 'agent.shell.run': 'bash'}
    from .worker import _RemoteOperation

    def is_helper(value):
        return isinstance(value, _RemoteOperation) or (inspect.ismethod(value) and isinstance(value.__self__, _RemoteOperation))

    active_helpers = {name for name in contract if is_helper(namespace.get(name))}
    from types import SimpleNamespace
    agent = namespace.get('agent')
    if type(agent) is not SimpleNamespace:
        aliases.clear()
    else:
        aliases = {path: name for path, name in aliases.items()
                   if type(vars(agent).get(path.split('.')[1])) is SimpleNamespace
                   and is_helper(vars(vars(agent)[path.split('.')[1]]).get(path.split('.')[2]))}
    uncertain = False

    def fail(node, kind, message):
        error = ValueError(f'cell.py:{node.lineno}:{node.col_offset + 1}: error[{kind}] {message}. No code in this cell executed')
        error.lineno = node.lineno
        raise error

    def infer(node):
        nonlocal uncertain
        if isinstance(node, (ast.IfExp, ast.BoolOp)):
            uncertain = True
            values.clear()
            active_helpers.clear()
            aliases.clear()
            return None  # Branch-dependent expressions may never evaluate their names.
        if isinstance(node, ast.Constant):
            return type(node.value).__name__
        if isinstance(node, ast.NamedExpr):
            inferred = infer(node.value)
            known.add(node.target.id)
            values[node.target.id] = inferred
            active_helpers.discard(node.target.id)
            if node.target.id == 'agent':
                aliases.clear()
            return inferred
        if isinstance(node, ast.Name):
            if not uncertain and node.id not in known:
                fail(node, 'unresolved-reference', f'Name {node.id!r} is not defined')
            return values.get(node.id)
        if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            uncertain = True
            values.clear()
            active_helpers.clear()
            aliases.clear()
            return None  # Nested scopes intentionally lose origin and type information.
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            helper = aliases.get(name, name if name in active_helpers else None)
            if helper in contract:
                spec = contract[helper]
                # **kwargs/*args are runtime-checked; literal explicit bad names still fail.
                for kw in node.keywords:
                    if kw.arg is not None and kw.arg not in spec['signature'].parameters:
                        fail(kw.value, 'unknown-argument', f'{name} has no argument {kw.arg!r}; valid: {spec["signature"]}')
                if not any(isinstance(a, ast.Starred) for a in node.args) and all(k.arg for k in node.keywords):
                    try:
                        bound = spec['signature'].bind(*node.args, **{k.arg: k.value for k in node.keywords})
                    except TypeError as error:
                        fail(node, 'signature', f'{name}{spec["signature"]}: {error}')
                    for key, arg in bound.arguments.items():
                        actual = infer(arg)
                        expected = str(spec['signature'].parameters[key].annotation)
                        if isinstance(actual, str) and expected in {'str', 'int', 'bool'} and actual != expected:
                            fail(arg, 'type-mismatch', f'{key} expects {expected}, got {actual}')
                return spec['result']
            infer(node.func)
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                infer(arg)
            if name not in namespace.get('__builtins__', {}):
                uncertain = True
                active_helpers.clear()
                aliases.clear()
                values.clear()
            # Calls can mutate dictionaries; drop all schema assumptions after opaque calls.
            for key in list(values):
                if isinstance(values[key], dict):
                    values.pop(key)
            return None
        if isinstance(node, ast.Subscript):
            origin = infer(node.value)
            if isinstance(origin, dict) and isinstance(node.slice, ast.Constant):
                key = node.slice.value
                if key not in origin:
                    suggestion = difflib.get_close_matches(str(key), origin, n=1)
                    fail(node, 'invalid-key', f'Unknown helper result key {key!r}' + (f'; did you mean {suggestion[0]!r}?' if suggestion else ''))
                return origin[key]
            infer(node.slice)
            return None
        if isinstance(node, ast.BinOp):
            left, right = infer(node.left), infer(node.right)
            if isinstance(node.op, ast.Add) and (left, right) in [('int', 'str'), ('str', 'int'), ('float', 'str'), ('str', 'float')]:
                fail(node, 'unsupported-operator', f'Operator + is not supported between {left} and {right}')
            return None
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                infer(child)
        return None

    for statement in tree.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            value = infer(statement.value) if statement.value else None
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    known.add(target.id)
                    values[target.id] = value
                    active_helpers.discard(target.id)
                    if target.id == 'agent':
                        aliases.clear()
                else:
                    # Mutations/unpacking invalidate provenance, never infer aliases through them.
                    values.clear()
                    active_helpers.clear()
                    aliases.clear()
                    known.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
        elif isinstance(statement, ast.Expr):
            infer(statement.value)
        elif isinstance(statement, (ast.Import, ast.ImportFrom)):
            imported = {a.asname or a.name.split('.')[0] for a in statement.names}
            known.update(imported)
            active_helpers.difference_update(imported)
            for name in imported:
                values.pop(name, None)
            if 'agent' in imported or '*' in imported:
                aliases.clear()
                uncertain = True
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            known.add(statement.name)
            values.pop(statement.name, None)
            active_helpers.discard(statement.name)
            if statement.name == 'agent' or statement.decorator_list:
                aliases.clear()
                active_helpers.clear()
                values.clear()
                uncertain = True
        else:
            # Branches, exec-like namespace changes, augmented writes: abstain after this point.
            uncertain = True
            values.clear()
            active_helpers.clear()
            aliases.clear()
