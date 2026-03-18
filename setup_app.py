from setuptools import setup

APP = ['sonos_keys.py']
OPTIONS = {
    'argv_emulation': False,
    'plist': {
        'CFBundleName': 'Sonos Keys',
        'CFBundleIdentifier': 'com.sonos-keys',
        'CFBundleVersion': '1.0.0',
        'LSUIElement': True,  # hide from dock, menu bar only
    },
    'packages': ['soco', 'rumps'],
}

setup(
    app=APP,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
