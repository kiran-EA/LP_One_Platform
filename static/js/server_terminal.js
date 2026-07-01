// Server File Browser JS
(function () {
    const DEFAULT_PATH = '/app/share/Informatica/scripts/bin';

    let currentPath = DEFAULT_PATH;
    let cmdHistory = [];
    let histIdx = -1;

    const fileList      = document.getElementById('fileList');
    const listStatus    = document.getElementById('listStatus');
    const breadcrumb    = document.getElementById('breadcrumb');
    const fileContent   = document.getElementById('fileContent');
    const viewingPath   = document.getElementById('viewingPath');
    const searchInput   = document.getElementById('searchInput');
    const searchBtn     = document.getElementById('searchBtn');
    const searchResults = document.getElementById('searchResults');
    const searchResultsList  = document.getElementById('searchResultsList');
    const searchResultsTitle = document.getElementById('searchResultsTitle');
    const closeSearch   = document.getElementById('closeSearch');
    const refreshBtn    = document.getElementById('refreshBtn');
    const copyPathBtn   = document.getElementById('copyPathBtn');
    const termInput     = document.getElementById('termInput');
    const termOutput    = document.getElementById('termOutput');
    const termRun       = document.getElementById('termRun');
    const termPrompt    = document.getElementById('termPrompt');
    const clearTermBtn  = document.getElementById('clearTermBtn');

    // ── Directory listing ──────────────────────────────────────────
    async function loadDir(path) {
        currentPath = path;
        listStatus.innerHTML = '<span class="spinner"></span> Loading...';
        fileList.innerHTML = '';
        renderBreadcrumb(path);
        termPrompt.textContent = `eapcprod:${path}$`;

        try {
            const res  = await fetch(`/api/server/ls?path=${encodeURIComponent(path)}`);
            const data = await res.json();
            if (data.error) { listStatus.textContent = '✗ ' + data.error; return; }

            const entries = data.entries || [];
            // Sort: dirs first, then files; hidden last
            entries.sort((a, b) => {
                if (a.is_hidden !== b.is_hidden) return a.is_hidden ? 1 : -1;
                if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
                return a.name.localeCompare(b.name);
            });

            // Back button
            if (path !== '/') {
                const parent = path.substring(0, path.lastIndexOf('/')) || '/';
                fileList.appendChild(makeEntry('..', true, '', '', () => loadDir(parent)));
            }

            entries.forEach(e => {
                const cb = e.is_dir
                    ? () => loadDir(path.replace(/\/$/, '') + '/' + e.name)
                    : () => loadFile(path.replace(/\/$/, '') + '/' + e.name);
                fileList.appendChild(makeEntry(e.name, e.is_dir, e.size, e.modified, cb));
            });

            listStatus.textContent = `${entries.length} items`;
        } catch (err) {
            listStatus.textContent = '✗ ' + err.message;
        }
    }

    function makeEntry(name, isDir, size, modified, onClick) {
        const el = document.createElement('div');
        el.className = 'file-entry';
        el.title = modified ? `${name}  (${size})  ${modified}` : name;

        const icon = document.createElement('span');
        icon.className = 'file-icon';
        icon.textContent = name === '..' ? '↩' : isDir ? '📁' : name.endsWith('.sh') ? '⚙' : name.endsWith('.sql') ? '🗄' : name.endsWith('.txt') ? '📄' : name.endsWith('.log') ? '📋' : '📄';

        const nameEl = document.createElement('span');
        nameEl.className = 'file-name ' + (isDir ? 'dir' : name.endsWith('.sh') ? 'sh' : 'file');
        nameEl.textContent = name;

        const sizeEl = document.createElement('span');
        sizeEl.className = 'file-size';
        sizeEl.textContent = size && !isDir && name !== '..' ? formatBytes(parseInt(size) || 0) : '';

        el.appendChild(icon);
        el.appendChild(nameEl);
        el.appendChild(sizeEl);
        el.addEventListener('click', () => {
            document.querySelectorAll('.file-entry.active').forEach(e => e.classList.remove('active'));
            el.classList.add('active');
            onClick();
        });
        return el;
    }

    // ── Breadcrumb ─────────────────────────────────────────────────
    function renderBreadcrumb(path) {
        breadcrumb.innerHTML = '';
        const parts = path.split('/').filter(Boolean);
        // root
        const root = document.createElement('button');
        root.className = 'path-crumb';
        root.textContent = '/';
        root.addEventListener('click', () => loadDir('/'));
        breadcrumb.appendChild(root);

        let built = '';
        parts.forEach((p, i) => {
            built += '/' + p;
            const sep = document.createElement('span');
            sep.className = 'path-sep';
            sep.textContent = '/';
            breadcrumb.appendChild(sep);
            const crumb = document.createElement('button');
            crumb.className = 'path-crumb';
            crumb.textContent = p;
            const target = built;
            crumb.addEventListener('click', () => loadDir(target));
            breadcrumb.appendChild(crumb);
        });
    }

    // ── File viewer ────────────────────────────────────────────────
    async function loadFile(path) {
        viewingPath.textContent = path;
        fileContent.innerHTML = '<div class="empty-state"><div class="spinner" style="width:20px;height:20px;border-width:3px;"></div><div>Loading...</div></div>';
        try {
            const res  = await fetch(`/api/server/cat?path=${encodeURIComponent(path)}`);
            const data = await res.json();
            if (data.error) {
                fileContent.innerHTML = `<div class="empty-state" style="color:#f85149;">✗ ${escHtml(data.error)}</div>`;
                return;
            }
            renderFileContent(data.content);
        } catch (err) {
            fileContent.innerHTML = `<div class="empty-state" style="color:#f85149;">✗ ${escHtml(err.message)}</div>`;
        }
    }

    function renderFileContent(text) {
        const lines = text.split('\n');
        fileContent.innerHTML = '';
        lines.forEach((line, i) => {
            const row = document.createElement('div');
            const ln  = document.createElement('span');
            ln.className = 'ln';
            ln.textContent = String(i + 1).padStart(4, ' ');
            const content = document.createElement('span');
            content.textContent = line;
            row.appendChild(ln);
            row.appendChild(content);
            fileContent.appendChild(row);
        });
    }

    // ── Search ─────────────────────────────────────────────────────
    async function doSearch() {
        const q = searchInput.value.trim();
        if (!q) return;
        searchResultsTitle.textContent = `Searching "${q}"...`;
        searchResults.style.display = 'flex';
        searchResultsList.innerHTML = '<div class="no-results"><span class="spinner"></span> Searching...</div>';

        try {
            const res  = await fetch(`/api/server/search?path=${encodeURIComponent(currentPath)}&q=${encodeURIComponent(q)}`);
            const data = await res.json();
            searchResultsTitle.textContent = `Results for "${q}" (${(data.results || []).length})`;
            searchResultsList.innerHTML = '';

            if (!data.results || data.results.length === 0) {
                searchResultsList.innerHTML = '<div class="no-results">No files found</div>';
                return;
            }
            data.results.forEach(fullPath => {
                const item = document.createElement('div');
                item.className = 'search-result-item';
                const dir  = fullPath.substring(0, fullPath.lastIndexOf('/'));
                const name = fullPath.substring(fullPath.lastIndexOf('/') + 1);
                item.innerHTML = `<span class="sr-dir">${escHtml(dir)}/</span><span class="sr-name">${escHtml(name)}</span>`;
                item.title = fullPath;
                item.addEventListener('click', () => {
                    searchResults.style.display = 'none';
                    // navigate to dir and open file
                    loadDir(dir).then(() => loadFile(fullPath));
                });
                searchResultsList.appendChild(item);
            });
        } catch (err) {
            searchResultsList.innerHTML = `<div class="no-results" style="color:#f85149;">✗ ${escHtml(err.message)}</div>`;
        }
    }

    // ── Terminal ───────────────────────────────────────────────────
    function termPrint(text, color) {
        const line = document.createElement('div');
        if (color) line.style.color = color;
        line.textContent = text;
        termOutput.appendChild(line);
        termOutput.scrollTop = termOutput.scrollHeight;
    }

    async function runCmd(cmd) {
        if (!cmd.trim()) return;
        cmdHistory.unshift(cmd);
        histIdx = -1;
        termPrint(`${termPrompt.textContent} ${cmd}`, '#58a6ff');

        // Handle cd locally (navigate sidebar)
        if (cmd.startsWith('cd ')) {
            const target = cmd.slice(3).trim();
            const newPath = target.startsWith('/') ? target : currentPath.replace(/\/$/, '') + '/' + target;
            await loadDir(newPath);
            termPrint(`Changed directory to ${newPath}`, '#3fb950');
            return;
        }

        try {
            const res  = await fetch('/api/server/exec', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cmd: `cd ${JSON.stringify(currentPath)} && ${cmd}` })
            });
            const data = await res.json();
            if (data.error) { termPrint('✗ ' + data.error, '#f85149'); return; }
            if (data.output) termPrint(data.output.trimEnd());
        } catch (err) {
            termPrint('✗ ' + err.message, '#f85149');
        }
    }

    // ── Utilities ──────────────────────────────────────────────────
    function escHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
    function formatBytes(b) {
        if (!b || b === 0) return '';
        if (b < 1024) return b + 'B';
        if (b < 1024*1024) return (b/1024).toFixed(1) + 'K';
        return (b/(1024*1024)).toFixed(1) + 'M';
    }

    // ── Event wiring ───────────────────────────────────────────────
    searchBtn.addEventListener('click', doSearch);
    searchInput.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
    closeSearch.addEventListener('click', () => { searchResults.style.display = 'none'; });

    refreshBtn.addEventListener('click', () => loadDir(currentPath));

    copyPathBtn.addEventListener('click', () => {
        const p = viewingPath.textContent;
        if (p && p !== '— select a file —') {
            navigator.clipboard.writeText(p).then(() => {
                copyPathBtn.textContent = '✓ Copied';
                setTimeout(() => { copyPathBtn.textContent = '⎘ Copy Path'; }, 1500);
            });
        }
    });

    termRun.addEventListener('click', () => {
        const cmd = termInput.value.trim();
        termInput.value = '';
        runCmd(cmd);
    });

    termInput.addEventListener('keydown', e => {
        if (e.key === 'Enter') {
            const cmd = termInput.value.trim();
            termInput.value = '';
            runCmd(cmd);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (histIdx < cmdHistory.length - 1) {
                histIdx++;
                termInput.value = cmdHistory[histIdx];
            }
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (histIdx > 0) { histIdx--; termInput.value = cmdHistory[histIdx]; }
            else { histIdx = -1; termInput.value = ''; }
        }
    });

    clearTermBtn.addEventListener('click', () => { termOutput.innerHTML = ''; });

    // ── Boot ───────────────────────────────────────────────────────
    loadDir(DEFAULT_PATH);
    termPrint('Connected to eapcprod@54.176.67.86', '#3fb950');
    termPrint('Type commands below. Use cd <path> to navigate. ↑/↓ for history.', '#6e7681');
})();
