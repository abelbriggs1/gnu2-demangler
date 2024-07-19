
# Possible Call Stack / Tree of GNU2 demangler...


```
internal_cplus_demangle
    -> gnu_special
        -> demangle_qualified
        -> demangle_template
            -> do_type
            -> demangle_template_template_parm
                -> demangle_template_template_parm
        -> internal_cplus_demangle
        -> do_type
    -> demangle_prefix
    -> demangle_signature
    -> mop_up
```
