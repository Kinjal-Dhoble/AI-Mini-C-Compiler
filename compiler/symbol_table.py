"""Reusable nested-scope symbol table used by compiler extensions."""
class SymbolTable:
    def __init__(self): self.scopes=[{}]
    def push_scope(self): self.scopes.append({})
    def pop_scope(self):
        if len(self.scopes)==1: raise RuntimeError('Cannot pop global scope')
        return self.scopes.pop()
    def declare(self,name,metadata):
        if name in self.scopes[-1]: return False
        self.scopes[-1][name]=metadata
        return True
    def lookup(self,name):
        for scope in reversed(self.scopes):
            if name in scope:return scope[name]
        return None
    @property
    def current_scope(self): return self.scopes[-1]
