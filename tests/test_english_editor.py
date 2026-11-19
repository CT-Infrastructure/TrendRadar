"""Behavior checks for the English editor and repository-local defaults."""

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for editor checks")
class EnglishEditorTests(unittest.TestCase):
    def test_language_preferences_and_storage_failure(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('docs/assets/i18n.js', 'utf8');
function create(saved, unavailable = false) {
    const context = {
        navigator: {language: 'zh-CN'},
        localStorage: {
            getItem() { if (unavailable) throw new Error('Storage blocked'); return saved; },
            setItem(key, value) { if (unavailable) throw new Error('Storage blocked'); saved = value; },
        },
        document: {
            documentElement: {}, readyState: 'loading', addEventListener() {},
            querySelectorAll() { return []; }, getElementById() { return null; },
        },
    };
    context.window = context;
    vm.createContext(context);
    vm.runInContext(source, context);
    return context;
}
const fresh = create(null);
assert.equal(fresh.getLang(), 'en');
assert.equal(fresh.document.documentElement.lang, 'en');
assert.equal(fresh.t('nav.copyConfig'), 'Copy Config');
const chinese = create('zh');
assert.equal(chinese.getLang(), 'zh');
assert.equal(chinese.t('nav.copyConfig'), '复制配置');
chinese.switchLang('en');
assert.equal(chinese.t('nav.copyConfig'), 'Copy Config');
const blocked = create(null, true);
assert.equal(blocked.getLang(), 'en');
blocked.switchLang('zh');
assert.equal(blocked.getLang(), 'zh');
// Every HTML and JavaScript translation key must be available in English.
const ui = fs.readFileSync('docs/index.html', 'utf8') + fs.readFileSync('docs/assets/script.js', 'utf8');
for (const match of ui.matchAll(/(?:data-i18n(?:-html|-placeholder|-title)?="|\bt\(')([\w.]+)/g)) {
    assert.notEqual(fresh.t(match[1]), match[1], 'Missing translation: ' + match[1]);
}
"""
        subprocess.run(["node", "-e", script], cwd=ROOT, check=True)

    def test_configuration_uses_checkout_then_fork_fallback(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('docs/assets/script.js', 'utf8').split('let currentYaml =')[0];
const calls = [];
const context = {
    URL, AbortController, setTimeout, clearTimeout, console,
    window: {location: {href: 'https://example.test/TrendRadar/docs/index.html'}},
    fetch: async url => { calls.push(url); return {ok: calls.length > 1}; },
    t: key => key,
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
    await vm.runInContext("fetchWithFallback(remoteConfigUrl('config'))", context);
    assert.equal(calls[0], 'https://example.test/TrendRadar/config/config.yaml');
    assert.equal(calls[1], 'https://raw.githubusercontent.com/CT-Infrastructure/TrendRadar/refs/heads/master/config/config.yaml');
    assert.equal(vm.runInContext("remoteConfigName('timeline')", context), 'timeline.yaml');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        subprocess.run(["node", "-e", script], cwd=ROOT, check=True)


if __name__ == "__main__":
    unittest.main()
