#!/usr/bin/env python
import os
import sys

# Python 3.14 Compatibility Patch - Apply before Django imports
if sys.version_info >= (3, 14):
    import copy as copy_module
    
    def _apply_python314_compat_patch():
        """Apply compatibility patch for Django with Python 3.14"""
        # Monkey-patch copy module's behavior for objects without __dict__
        _original_copy = copy_module.copy
        
        def patched_copy(x):
            try:
                return _original_copy(x)
            except (AttributeError, TypeError) as e:
                # If copy fails due to __dict__ issues, try alternative approaches
                if "__dict__" in str(e) or "dicts" in str(e):
                    # For Django Context objects, manually handle the copy
                    if hasattr(x, '__class__'):
                        try:
                            # Create a new instance without calling __init__
                            new_obj = x.__class__.__new__(x.__class__)
                            # Copy attributes
                            if hasattr(x, '__dict__'):
                                new_obj.__dict__.update(x.__dict__)
                            # Copy __slots__ if present
                            if hasattr(x.__class__, '__slots__'):
                                for attr in x.__class__.__slots__:
                                    if hasattr(x, attr):
                                        setattr(new_obj, attr, getattr(x, attr))
                            return new_obj
                        except Exception:
                            pass
                raise
        
        copy_module.copy = patched_copy
    
    _apply_python314_compat_patch()

def main() -> None:
    # Load environment variables from .env file
    from pathlib import Path
    env_file = Path(__file__).resolve().parent / '.env'
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)
    
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Is it installed and available on your "
            "PYTHONPATH environment variable? Did you forget to activate a "
            "virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
