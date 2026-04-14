from django import template

register = template.Library()

@register.filter(name='dict_key')
def dict_key(d, key):
    """Access dictionary key in templates"""
    try:
        return d.get(key, [])
    except (AttributeError, TypeError):
        return []
