// Dashboard page JavaScript
document.addEventListener('DOMContentLoaded', function() {
    // Campaign Management iframe error fallback
    const campaignIframe = document.querySelector('.campaign-iframe');
    if (campaignIframe) {
        campaignIframe.addEventListener('error', function() {
            campaignIframe.style.display = 'none';
            document.getElementById('campaignIframeError').style.display = 'flex';
        });
    }

    // Handle bfcache (back/forward cache)
    window.addEventListener('pageshow', function(event) {
        if (event.persisted) {
            window.location.reload();
        }
    });
    
    // Sidebar nav switching
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            navItems.forEach(n => n.classList.remove('active'));
            document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
            item.classList.add('active');
            document.getElementById(item.dataset.panel).classList.add('active');
        });
    });
    
    // Nested sub-tab switching (used by Daily QC Monitor)
    function wireSubTabGroup(buttonSelector, contentClass, dataAttr) {
        document.querySelectorAll(buttonSelector).forEach(btn => {
            btn.addEventListener('click', () => {
                const bar = btn.parentElement;
                const panelHost = bar.parentElement;
                bar.querySelectorAll(buttonSelector).forEach(b => b.classList.remove('active'));
                panelHost.querySelectorAll(':scope > .' + contentClass).forEach(c => c.classList.remove('active'));
                btn.classList.add('active');
                document.getElementById(btn.dataset[dataAttr]).classList.add('active');
            });
        });
    }
    wireSubTabGroup('.subtab-button', 'subtab-content', 'subtab');
    wireSubTabGroup('.subsubtab-button', 'subsubtab-content', 'subsubtab');

    // Mail File Validation functionality
    const loadFilesBtn = document.getElementById('loadFilesBtn');
    const loadFilesText = document.getElementById('loadFilesText');
    const loadFilesLoader = document.getElementById('loadFilesLoader');
    const fileListContainer = document.getElementById('fileListContainer');
    const fileList = document.getElementById('fileList');
    const connectionStatus = document.getElementById('connectionStatus');
    const errorDisplay = document.getElementById('errorDisplay');
    const selectionCount = document.getElementById('selectionCount');
    const proceedBtn = document.getElementById('proceedBtn');
    const proceedText = document.getElementById('proceedText');
    const proceedLoader = document.getElementById('proceedLoader');
    const resultsContainer = document.getElementById('resultsContainer');
    const resultsContent = document.getElementById('resultsContent');
    const clearResultsBtn = document.getElementById('clearResultsBtn');
    
    let selectedFiles = [];
    let selectedFileSizes = {};  // filename -> bytes

    // Load files from SFTP
    loadFilesBtn.addEventListener('click', async function() {
        loadFilesText.textContent = 'Connecting to SFTP...';
        loadFilesLoader.style.display = 'inline-block';
        loadFilesBtn.disabled = true;
        errorDisplay.style.display = 'none';
        fileListContainer.style.display = 'none';

        try {
            const response = await fetch('/api/list-files?path=/FromLP/Catalog Mail Files');
            const data = await response.json();

            if (data.error) {
                errorDisplay.textContent = data.error;
                errorDisplay.style.display = 'block';
                connectionStatus.textContent = 'Connection Failed';
                connectionStatus.classList.remove('connected');
            } else {
                displayFiles(data.files);
                connectionStatus.textContent = 'Connected';
                connectionStatus.classList.add('connected');
                fileListContainer.style.display = 'block';
            }
        } catch (error) {
            errorDisplay.textContent = 'Error connecting to SFTP: ' + error.message;
            errorDisplay.style.display = 'block';
            connectionStatus.textContent = 'Connection Failed';
            connectionStatus.classList.remove('connected');
        } finally {
            loadFilesText.textContent = 'Load Files from SFTP';
            loadFilesLoader.style.display = 'none';
            loadFilesBtn.disabled = false;
        }
    });
    
    // Display files in the list
    function displayFiles(files) {
        fileList.innerHTML = '';
        selectedFiles = [];
        
        if (!files || files.length === 0) {
            fileList.innerHTML = '<div style="padding: 2rem; text-align: center; color: var(--gray-600);">No files found</div>';
            return;
        }
        
        files.forEach(file => {
            const fileItem = document.createElement('div');
            fileItem.className = 'file-item';

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.value = file.name;
            checkbox.dataset.size = file.size || 0;
            checkbox.addEventListener('change', updateSelection);
            
            const fileInfo = document.createElement('div');
            fileInfo.className = 'file-info';
            
            const fileName = document.createElement('div');
            fileName.className = 'file-name';
            fileName.textContent = file.name;
            
            const fileMeta = document.createElement('div');
            fileMeta.className = 'file-meta';
            
            const size = formatBytes(file.size);
            const modified = file.modified;
            
            fileMeta.innerHTML = `
                <span>Size: ${size}</span>
                <span>Modified: ${modified}</span>
            `;
            
            fileInfo.appendChild(fileName);
            fileInfo.appendChild(fileMeta);
            
            fileItem.appendChild(checkbox);
            fileItem.appendChild(fileInfo);
            
            fileList.appendChild(fileItem);
        });
        
        updateSelection();
    }
    
    // Update selection count
    function updateSelection() {
        const checkboxes = fileList.querySelectorAll('input[type="checkbox"]:checked');
        selectedFiles = Array.from(checkboxes).map(cb => cb.value);
        selectedFileSizes = {};
        checkboxes.forEach(cb => { selectedFileSizes[cb.value] = parseInt(cb.dataset.size) || 0; });

        selectionCount.textContent = `${selectedFiles.length} file${selectedFiles.length !== 1 ? 's' : ''} selected`;
        proceedBtn.disabled = selectedFiles.length === 0;
    }

    function estimateSeconds(totalBytes) {
        // ~100 KB/s actual observed SFTP speed on this server
        const est = Math.ceil(totalBytes / (100 * 1024));
        return Math.max(15, Math.ceil(est / 30) * 30);  // round to nearest 30s, min 15s
    }

    function formatTime(seconds) {
        if (seconds < 60) return `${seconds}s`;
        return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    }
    
    // Process selected files
    proceedBtn.addEventListener('click', async function() {
        if (selectedFiles.length === 0) return;

        proceedText.textContent = 'Processing for Result...';
        proceedLoader.style.display = 'inline-block';
        proceedBtn.disabled = true;

        // Estimated time + elapsed timer
        const totalBytes = Object.values(selectedFileSizes).reduce((a, b) => a + b, 0);
        const estSecs = estimateSeconds(totalBytes);

        let statusBar = document.getElementById('processingStatusBar');
        if (!statusBar) {
            statusBar = document.createElement('div');
            statusBar.id = 'processingStatusBar';
            statusBar.className = 'processing-status-bar';
            proceedBtn.parentNode.insertBefore(statusBar, proceedBtn.nextSibling);
        }

        let elapsed = 0;
        statusBar.innerHTML = `
            <span class="proc-label">Estimated: <strong>~${formatTime(estSecs)}</strong></span>
            <span class="proc-sep">·</span>
            <span class="proc-label">Elapsed: <strong id="elapsedTimer">0s</strong></span>
        `;
        statusBar.style.display = 'flex';

        const timer = setInterval(() => {
            elapsed++;
            const el = document.getElementById('elapsedTimer');
            if (el) el.textContent = formatTime(elapsed);
        }, 1000);

        try {
            const response = await fetch('/api/process-files', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ files: selectedFiles, path: '/FromLP/Catalog Mail Files' })
            });
            const { job_id, error } = await response.json();
            if (error) throw new Error(error);

            // Poll for job completion
            while (true) {
                await new Promise(r => setTimeout(r, 3000));
                const poll = await fetch(`/api/job-status/${job_id}`);
                const job  = await poll.json();
                const el = document.getElementById('elapsedTimer');

                if (job.progress) {
                    const bar = document.getElementById('processingStatusBar');
                    if (bar) {
                        bar.innerHTML = `
                            <span class="proc-label">Estimated: <strong>~${formatTime(estSecs)}</strong></span>
                            <span class="proc-sep">·</span>
                            <span class="proc-label">Elapsed: <strong id="elapsedTimer">${formatTime(elapsed)}</strong></span>
                            <span class="proc-sep">·</span>
                            <span class="proc-label" style="color:#2563eb;">${job.progress}</span>`;
                    }
                }

                if (job.status === 'done') {
                    displayResults(job.results);
                    resultsContainer.style.display = 'block';
                    resultsContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    break;
                }
                if (job.status === 'error') {
                    alert('Processing error: ' + (job.error || 'Unknown error'));
                    break;
                }
            }
        } catch (error) {
            alert('Error processing files: ' + error.message);
        } finally {
            clearInterval(timer);
            statusBar.style.display = 'none';
            proceedText.textContent = 'Proceed';
            proceedLoader.style.display = 'none';
            proceedBtn.disabled = false;
        }
    });
    
    // Display processing results
    function displayResults(results) {
        resultsContent.innerHTML = '';
        
        results.forEach(result => {
            if (result.error) {
                const errorCard = document.createElement('div');
                errorCard.className = 'result-card';
                errorCard.innerHTML = `
                    <div class="result-header">
                        <h4>Error</h4>
                    </div>
                    <div class="result-body">
                        <div class="error-display" style="display: block;">
                            ${result.error}
                        </div>
                    </div>
                `;
                resultsContent.appendChild(errorCard);
            } else {
                const card = document.createElement('div');
                card.className = 'result-card';
                
                const header = document.createElement('div');
                header.className = 'result-header';
                header.innerHTML = `<h4>${result.zip_file}</h4>`;
                
                const body = document.createElement('div');
                body.className = 'result-body';
                
                function inlineBadge(pass) {
                    return `<span class="${pass ? 'badge-pass' : 'badge-fail'}" style="font-size:0.7rem;padding:0.1rem 0.45rem;">${pass ? 'PASS' : 'FAIL'}</span>`;
                }

                // Track whether every file in this ZIP passes all rules
                let allCardPass = result.files.length > 0;

                result.files.forEach(file => {
                    const fileResult = document.createElement('div');
                    fileResult.className = 'file-result';

                    const fileHeader = document.createElement('div');
                    fileHeader.className = 'file-result-header';

                    const fileName = document.createElement('div');
                    fileName.className = 'file-result-name';
                    fileName.textContent = file.filename;

                    const status = document.createElement('div');
                    status.className = file.status === 'success' ? 'status-success' : 'status-error';
                    status.textContent = file.status === 'success' ? '✓ Success' : '✗ ' + file.status;

                    fileHeader.appendChild(fileName);
                    fileHeader.appendChild(status);
                    fileResult.appendChild(fileHeader);

                    if (file.status === 'success' && file.header && file.header.length > 0) {
                        const headerDisplay = document.createElement('div');
                        headerDisplay.className = 'header-display';

                        const dataRows   = file.row_count > 0 ? file.row_count - 1 : 0;
                        const rowsPass   = dataRows > 100;
                        const delimPass  = file.delimiter === 'Comma (,)';
                        const custnoVal  = file.custno_null_pct != null ? file.custno_null_pct : null;
                        const keycodeVal = file.keycode_null_pct != null ? file.keycode_null_pct : null;
                        const custnoPass  = custnoVal !== null ? custnoVal <= 5 : true;
                        const keycodePass = keycodeVal !== null ? keycodeVal <= 5 : true;

                        if (!(rowsPass && delimPass && custnoPass && keycodePass)) allCardPass = false;

                        // Column validation badge
                        const colValidation = document.createElement('div');
                        colValidation.className = 'validation-row';
                        const validBadge = document.createElement('span');
                        validBadge.className = file.columns_valid ? 'badge-pass' : 'badge-fail';
                        validBadge.textContent = file.columns_valid ? 'PASS' : 'FAIL';
                        colValidation.innerHTML = '<span class="validation-label">Column Names:</span> ';
                        colValidation.appendChild(validBadge);
                        headerDisplay.appendChild(colValidation);

                        const headerGrid = document.createElement('div');
                        headerGrid.className = 'header-grid';
                        file.header.forEach(col => {
                            const colDiv = document.createElement('div');
                            colDiv.className = 'header-col';
                            colDiv.textContent = col;
                            headerGrid.appendChild(colDiv);
                        });
                        headerDisplay.appendChild(headerGrid);

                        const statsRow = document.createElement('div');
                        statsRow.className = 'stats-row';
                        statsRow.innerHTML = `
                            <span class="stat-item">Total rows: <strong class="${rowsPass ? 'stat-ok' : 'stat-ng'}">${file.row_count}</strong> ${inlineBadge(rowsPass)}</span>
                            <span class="stat-sep">|</span>
                            <span class="stat-item">Delimiter: <strong class="${delimPass ? 'stat-ok' : 'stat-ng'}">${file.delimiter || 'Unknown'}</strong> ${inlineBadge(delimPass)}</span>
                            <span class="stat-sep">|</span>
                            <span class="stat-item">CustNo null: <strong class="${custnoPass ? 'stat-ok' : 'stat-ng'}">${custnoVal !== null ? custnoVal + '%' : 'N/A'}</strong> ${inlineBadge(custnoPass)}</span>
                            <span class="stat-sep">|</span>
                            <span class="stat-item">Keycode null: <strong class="${keycodePass ? 'stat-ok' : 'stat-ng'}">${keycodeVal !== null ? keycodeVal + '%' : 'N/A'}</strong> ${inlineBadge(keycodePass)}</span>
                        `;
                        headerDisplay.appendChild(statsRow);
                        fileResult.appendChild(headerDisplay);
                    } else {
                        allCardPass = false;
                    }

                    body.appendChild(fileResult);
                });

                // Single Load button per ZIP — only if every file passed
                if (allCardPass) {
                    const loadWrap = document.createElement('div');
                    loadWrap.style.padding = '1rem 0 0.25rem';
                    const loadBtn = document.createElement('button');
                    loadBtn.className = 'btn-success mf-load-trigger';
                    loadBtn.style.width = '100%';
                    loadBtn.textContent = 'Load';
                    loadWrap.appendChild(loadBtn);
                    body.appendChild(loadWrap);
                }

                card.appendChild(header);
                card.appendChild(body);

                resultsContent.appendChild(card);
            }
        });
    }

    // Clear results
    clearResultsBtn.addEventListener('click', function() {
        resultsContainer.style.display = 'none';
        resultsContent.innerHTML = '';
    });

    // ==================== Mail File Load Modal ====================
    const mfLoadModal    = document.getElementById('mfLoadModal');
    const mfModalCloseBtn = document.getElementById('mfModalClose');
    const mfRunScriptBtn  = document.getElementById('mfRunScriptBtn');

    resultsContent.addEventListener('click', function (e) {
        const btn = e.target.closest('.mf-load-trigger');
        if (!btn) return;
        mfLoadModal.style.display = 'flex';
        document.getElementById('mfFormSection').style.display = 'block';
        document.getElementById('mfLogSection').style.display = 'none';
    });

    mfModalCloseBtn.addEventListener('click', function () {
        mfLoadModal.style.display = 'none';
    });

    mfLoadModal.addEventListener('click', function (e) {
        if (e.target === mfLoadModal) mfLoadModal.style.display = 'none';
    });

    mfRunScriptBtn.addEventListener('click', async function () {
        const campName = document.getElementById('mfCampName').value.trim();
        if (!campName) { alert('Please enter a Campaign Name.'); return; }

        const res = await fetch('/api/mailfile/start-script', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ camp_name: campName })
        });
        const data = await res.json();
        if (data.error) { alert('Error: ' + data.error); return; }

        document.getElementById('mfFormSection').style.display = 'none';
        const logSection  = document.getElementById('mfLogSection');
        logSection.style.display = 'block';
        const logTerminal = document.getElementById('mfLogTerminal');
        const logStatus   = document.getElementById('mfLogStatus');
        logTerminal.innerHTML = '';
        logStatus.className = 'log-status-running';
        logStatus.textContent = 'Running...';

        const evtSource = new EventSource('/api/mailfile/stream');
        evtSource.onmessage = function (e) {
            const msg = JSON.parse(e.data);
            const line = document.createElement('div');
            line.className = 'log-line';
            line.textContent = msg.line;
            logTerminal.appendChild(line);
            logTerminal.scrollTop = logTerminal.scrollHeight;
            if (msg.done) {
                evtSource.close();
                logStatus.className = 'log-status-done';
                logStatus.textContent = 'Done';
            }
        };
        evtSource.onerror = function () {
            evtSource.close();
            logStatus.className = 'log-status-error';
            logStatus.textContent = 'Connection error';
        };
    });
    
    // Utility function to format bytes
    function formatBytes(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
    }

    // ==================== CIRCPLAN TAB ====================

    const cpLoadFilesBtn      = document.getElementById('cpLoadFilesBtn');
    const cpLoadFilesText     = document.getElementById('cpLoadFilesText');
    const cpLoadFilesLoader   = document.getElementById('cpLoadFilesLoader');
    const cpConnectionStatus  = document.getElementById('cpConnectionStatus');
    const cpErrorDisplay      = document.getElementById('cpErrorDisplay');
    const cpFileListContainer = document.getElementById('cpFileListContainer');
    const cpFileList          = document.getElementById('cpFileList');
    const cpSelectionCount    = document.getElementById('cpSelectionCount');
    const cpProceedBtn        = document.getElementById('cpProceedBtn');
    const cpProceedText       = document.getElementById('cpProceedText');
    const cpProceedLoader     = document.getElementById('cpProceedLoader');
    const cpResultsContainer  = document.getElementById('cpResultsContainer');
    const cpResultsContent    = document.getElementById('cpResultsContent');
    const cpClearResultsBtn   = document.getElementById('cpClearResultsBtn');

    let cpSelectedFiles = [];
    let cpSelectedSizes = {};

    cpLoadFilesBtn.addEventListener('click', async function () {
        cpLoadFilesText.textContent = 'Connecting to SFTP...';
        cpLoadFilesLoader.style.display = 'inline-block';
        cpLoadFilesBtn.disabled = true;
        cpErrorDisplay.style.display = 'none';
        cpFileListContainer.style.display = 'none';

        try {
            const response = await fetch('/api/circplan/list-files');
            const data = await response.json();

            if (data.error) {
                cpErrorDisplay.textContent = data.error;
                cpErrorDisplay.style.display = 'block';
                cpConnectionStatus.textContent = 'Connection Failed';
                cpConnectionStatus.classList.remove('connected');
            } else {
                cpDisplayFiles(data.files);
                cpConnectionStatus.textContent = 'Connected';
                cpConnectionStatus.classList.add('connected');
                cpFileListContainer.style.display = 'block';
            }
        } catch (err) {
            cpErrorDisplay.textContent = 'Error: ' + err.message;
            cpErrorDisplay.style.display = 'block';
            cpConnectionStatus.textContent = 'Connection Failed';
            cpConnectionStatus.classList.remove('connected');
        } finally {
            cpLoadFilesText.textContent = 'Load Files from SFTP';
            cpLoadFilesLoader.style.display = 'none';
            cpLoadFilesBtn.disabled = false;
        }
    });

    function cpDisplayFiles(files) {
        cpFileList.innerHTML = '';
        cpSelectedFiles = [];
        if (!files || files.length === 0) {
            cpFileList.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--gray-600);">No files found</div>';
            return;
        }
        files.forEach(file => {
            const item = document.createElement('div');
            item.className = 'file-item';

            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = file.name;
            cb.dataset.size = file.size || 0;
            cb.addEventListener('change', cpUpdateSelection);

            const info = document.createElement('div');
            info.className = 'file-info';

            const name = document.createElement('div');
            name.className = 'file-name';
            name.textContent = file.name;

            const meta = document.createElement('div');
            meta.className = 'file-meta';
            meta.innerHTML = `<span>Size: ${formatBytes(file.size)}</span><span>Modified: ${file.modified}</span>`;

            info.appendChild(name);
            info.appendChild(meta);
            item.appendChild(cb);
            item.appendChild(info);
            cpFileList.appendChild(item);
        });
        cpUpdateSelection();
    }

    function cpUpdateSelection() {
        const checked = cpFileList.querySelectorAll('input[type="checkbox"]:checked');
        cpSelectedFiles = Array.from(checked).map(cb => cb.value);
        cpSelectedSizes = {};
        checked.forEach(cb => { cpSelectedSizes[cb.value] = parseInt(cb.dataset.size) || 0; });
        cpSelectionCount.textContent = `${cpSelectedFiles.length} file${cpSelectedFiles.length !== 1 ? 's' : ''} selected`;
        cpProceedBtn.disabled = cpSelectedFiles.length === 0;
    }

    cpProceedBtn.addEventListener('click', async function () {
        if (cpSelectedFiles.length === 0) return;

        cpProceedText.textContent = 'Processing for Result...';
        cpProceedLoader.style.display = 'inline-block';
        cpProceedBtn.disabled = true;

        const totalBytes = Object.values(cpSelectedSizes).reduce((a, b) => a + b, 0);
        const estSecs = estimateSeconds(totalBytes);
        let cpStatusBar = document.getElementById('cpProcessingStatusBar');
        if (!cpStatusBar) {
            cpStatusBar = document.createElement('div');
            cpStatusBar.id = 'cpProcessingStatusBar';
            cpStatusBar.className = 'processing-status-bar';
            cpProceedBtn.parentNode.insertBefore(cpStatusBar, cpProceedBtn.nextSibling);
        }
        let elapsed = 0;
        cpStatusBar.innerHTML = `
            <span class="proc-label">Estimated: <strong>~${formatTime(estSecs)}</strong></span>
            <span class="proc-sep">·</span>
            <span class="proc-label">Elapsed: <strong id="cpElapsedTimer">0s</strong></span>`;
        cpStatusBar.style.display = 'flex';
        const timer = setInterval(() => {
            elapsed++;
            const el = document.getElementById('cpElapsedTimer');
            if (el) el.textContent = formatTime(elapsed);
        }, 1000);

        try {
            const response = await fetch('/api/circplan/process-files', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ files: cpSelectedFiles })
            });
            const { job_id, error } = await response.json();
            if (error) throw new Error(error);

            while (true) {
                await new Promise(r => setTimeout(r, 3000));
                const poll = await fetch(`/api/job-status/${job_id}`);
                const job  = await poll.json();

                if (job.progress) {
                    cpStatusBar.innerHTML = `
                        <span class="proc-label">Estimated: <strong>~${formatTime(estSecs)}</strong></span>
                        <span class="proc-sep">·</span>
                        <span class="proc-label">Elapsed: <strong id="cpElapsedTimer">${formatTime(elapsed)}</strong></span>
                        <span class="proc-sep">·</span>
                        <span class="proc-label" style="color:#2563eb;">${job.progress}</span>`;
                }

                if (job.status === 'done') {
                    cpDisplayResults(job.results);
                    cpResultsContainer.style.display = 'block';
                    cpResultsContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    break;
                }
                if (job.status === 'error') {
                    alert('Processing error: ' + (job.error || 'Unknown error'));
                    break;
                }
            }
        } catch (err) {
            alert('Error: ' + err.message);
        } finally {
            clearInterval(timer);
            cpStatusBar.style.display = 'none';
            cpProceedText.textContent = 'Proceed';
            cpProceedLoader.style.display = 'none';
            cpProceedBtn.disabled = false;
        }
    });

    function cpDisplayResults(results) {
        cpResultsContent.innerHTML = '';
        results.forEach(result => {
            if (result.error) {
                const card = document.createElement('div');
                card.className = 'result-card';
                card.innerHTML = `<div class="result-header"><h4>Error</h4></div>
                    <div class="result-body"><div class="error-display" style="display:block;">${result.error}</div></div>`;
                cpResultsContent.appendChild(card);
                return;
            }

            const card = document.createElement('div');
            card.className = 'result-card';
            card.innerHTML = `<div class="result-header"><h4>${result.zip_file}</h4></div>`;

            const body = document.createElement('div');
            body.className = 'result-body';

            result.files.forEach(file => {
                const fileResult = document.createElement('div');
                fileResult.className = 'file-result';

                const fileHeader = document.createElement('div');
                fileHeader.className = 'file-result-header';
                fileHeader.innerHTML = `
                    <div class="file-result-name">${file.filename}</div>
                    <div class="${file.status === 'success' ? 'status-success' : 'status-error'}">
                        ${file.status === 'success' ? '✓ Success' : '✗ ' + file.status}
                    </div>`;
                fileResult.appendChild(fileHeader);

                if (file.status === 'success' && file.header && file.header.length > 0) {
                    const headerDisplay = document.createElement('div');
                    headerDisplay.className = 'header-display';

                    // QC checks
                    const delimPass  = (file.delimiter || '').indexOf('Pipe') !== -1;
                    const colsPass   = file.columns_valid === true;
                    const keyCodeVal = (file.keycode_null_pct !== null && file.keycode_null_pct !== undefined) ? file.keycode_null_pct : null;
                    const keyPass    = keyCodeVal !== null ? keyCodeVal <= 5 : true;
                    const allPass    = delimPass && colsPass && keyPass;

                    function rIcon(pass) {
                        return `<span class="rule-icon ${pass ? 'rule-pass' : 'rule-fail'}">${pass ? '✓' : '✗'}</span>`;
                    }

                    const ruleBox = document.createElement('div');
                    ruleBox.className = `rule-box ${allPass ? 'rule-box-pass' : 'rule-box-fail'}`;
                    ruleBox.innerHTML = `
                        <div class="rule-title">
                            QC Validation
                            <span class="${allPass ? 'badge-pass' : 'badge-fail'}">${allPass ? 'ALL PASS' : 'FAILED'}</span>
                        </div>
                        <div class="rule-list">
                            <div class="rule-row">
                                ${rIcon(delimPass)}
                                <span class="rule-label">Delimiter is Pipe (|)</span>
                                <span class="${delimPass ? 'rule-detail' : 'rule-detail-fail'}">${file.delimiter || 'Unknown'}</span>
                            </div>
                            <div class="rule-row">
                                ${rIcon(colsPass)}
                                <span class="rule-label">Column names match</span>
                                <span class="${colsPass ? 'rule-detail' : 'rule-detail-fail'}">${colsPass ? 'Exact match' : 'Mismatch'}</span>
                            </div>
                            <div class="rule-row">
                                ${rIcon(keyPass)}
                                <span class="rule-label">Key Code null ≤ 5%</span>
                                <span class="${keyPass ? 'rule-detail' : 'rule-detail-fail'}">${keyCodeVal !== null ? keyCodeVal + '%' : 'N/A'}</span>
                            </div>
                        </div>`;
                    headerDisplay.appendChild(ruleBox);

                    // Header columns grid
                    const grid = document.createElement('div');
                    grid.className = 'header-grid';
                    file.header.forEach(col => {
                        const colDiv = document.createElement('div');
                        colDiv.className = 'header-col';
                        colDiv.textContent = col;
                        grid.appendChild(colDiv);
                    });
                    headerDisplay.appendChild(grid);

                    // Stats row
                    const stats = document.createElement('div');
                    stats.className = 'stats-row';
                    stats.innerHTML = `
                        <span class="stat-item">Total rows: <strong>${file.row_count}</strong></span>
                        <span class="stat-sep">|</span>
                        <span class="stat-item">Delimiter: <strong class="${delimPass ? 'stat-ok' : 'stat-ng'}">${file.delimiter || 'Unknown'}</strong></span>
                        <span class="stat-sep">|</span>
                        <span class="stat-item">Key Code null: <strong class="${keyPass ? 'stat-ok' : 'stat-ng'}">${keyCodeVal !== null ? keyCodeVal + '%' : 'N/A'}</strong> <span class="${keyPass ? 'badge-pass' : 'badge-fail'}" style="font-size:0.7rem;padding:0.1rem 0.45rem;">${keyPass ? 'PASS' : 'FAIL'}</span></span>`;
                    headerDisplay.appendChild(stats);

                    // Load button — only if all QC rules pass
                    if (allPass) {
                        const loadWrap = document.createElement('div');
                        loadWrap.style.marginTop = '1rem';
                        const loadBtn = document.createElement('button');
                        loadBtn.className = 'btn-success cp-load-trigger';
                        loadBtn.dataset.filename = file.filename;
                        loadBtn.style.width = '100%';
                        loadBtn.textContent = 'Load';
                        loadWrap.appendChild(loadBtn);
                        headerDisplay.appendChild(loadWrap);
                    }

                    fileResult.appendChild(headerDisplay);
                }
                body.appendChild(fileResult);
            });

            card.appendChild(body);
            cpResultsContent.appendChild(card);
        });
    }

    cpClearResultsBtn.addEventListener('click', function () {
        cpResultsContainer.style.display = 'none';
        cpResultsContent.innerHTML = '';
    });

    // ==================== CircPlan Load Modal ====================
    const cpLoadModal    = document.getElementById('cpLoadModal');
    const cpModalCloseBtn = document.getElementById('cpModalClose');
    const cpZipTypeSelect = document.getElementById('cpZipType');
    const cpRunScriptBtn  = document.getElementById('cpRunScriptBtn');

    // Delegate Load button clicks from results
    cpResultsContent.addEventListener('click', function (e) {
        const btn = e.target.closest('.cp-load-trigger');
        if (!btn) return;
        cpLoadModal.style.display = 'flex';
        document.getElementById('cpFormSection').style.display = 'block';
        document.getElementById('cpLogSection').style.display = 'none';
    });

    cpModalCloseBtn.addEventListener('click', function () {
        cpLoadModal.style.display = 'none';
    });

    cpLoadModal.addEventListener('click', function (e) {
        if (e.target === cpLoadModal) cpLoadModal.style.display = 'none';
    });

    cpZipTypeSelect.addEventListener('change', function () {
        if (cpZipTypeSelect.value === 'combined') {
            document.getElementById('cpMailFileSingleGroup').style.display = 'block';
            document.getElementById('cpMailFileMultiGroup').style.display = 'none';
        } else {
            document.getElementById('cpMailFileSingleGroup').style.display = 'none';
            document.getElementById('cpMailFileMultiGroup').style.display = 'block';
        }
    });

    cpRunScriptBtn.addEventListener('click', async function () {
        const campName    = document.getElementById('cpCampName').value.trim();
        const isNtf       = document.getElementById('cpIsNtf').value;
        const keycodeFile = document.getElementById('cpKeycodeFile').value.trim();
        const zipType     = cpZipTypeSelect.value;
        const mailFile    = document.getElementById('cpMailFile').value.trim();
        const mailFiles   = document.getElementById('cpMailFiles').value.trim();

        if (!campName || !keycodeFile) {
            alert('Please fill in Campaign Name and Keycode File Name.');
            return;
        }
        const mailFileValue = zipType === 'combined' ? mailFile : mailFiles;
        if (!mailFileValue) {
            alert('Please fill in the mail file name(s).');
            return;
        }

        const res = await fetch('/api/circplan/start-script', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ camp_name: campName, is_ntf: isNtf, keycode_file: keycodeFile, zip_type: zipType, mail_file: mailFileValue })
        });
        const data = await res.json();
        if (data.error) { alert('Error: ' + data.error); return; }

        document.getElementById('cpFormSection').style.display = 'none';
        const logSection  = document.getElementById('cpLogSection');
        logSection.style.display = 'block';
        const logTerminal = document.getElementById('cpLogTerminal');
        const logStatus   = document.getElementById('cpLogStatus');
        logTerminal.innerHTML = '';
        logStatus.className = 'log-status-running';
        logStatus.textContent = 'Running...';

        const evtSource = new EventSource('/api/circplan/stream');
        evtSource.onmessage = function (e) {
            const msg = JSON.parse(e.data);
            const line = document.createElement('div');
            line.className = 'log-line';
            line.textContent = msg.line;
            logTerminal.appendChild(line);
            logTerminal.scrollTop = logTerminal.scrollHeight;
            if (msg.done) {
                evtSource.close();
                logStatus.className = 'log-status-done';
                logStatus.textContent = 'Done';
            }
        };
        evtSource.onerror = function () {
            evtSource.close();
            logStatus.className = 'log-status-error';
            logStatus.textContent = 'Connection error';
        };
    });

    // ==================== DAILY QC MONITOR ====================
    const QC_PILL_LABELS = { ok: 'OK', missing: 'MISSING', flagged: 'LOW COUNT', unexpected: 'UNEXPECTED', low: 'LOW COUNT', high: 'HIGH COUNT' };

    // Each configured feed maps to one set of qc<Prefix>* elements in the HTML.
    // Add a new entry here (plus the matching panel markup) to wire up a new feed.
    const QC_FEEDS = [
        { prefix: 'qcWeb',              endpoint: '/api/qc/web-data-files',      name: 'Web Data Files' },
        { prefix: 'qcPos',              endpoint: '/api/qc/pos-data-files',      name: 'POS Data Files' },
        { prefix: 'qcEmailBluecore',    endpoint: '/api/qc/email-bluecore',      name: 'Bluecore Email' },
        { prefix: 'qcEmailWunderkind',  endpoint: '/api/qc/email-wunderkind',    name: 'Wunderkind Email & SMS' },
        { prefix: 'qcOutLiveramp',   endpoint: '/api/qc/outgoing-liveramp',   name: 'LiveRamp CRM',         cardId: 'qcOutCard_liveramp',  outgoing: true },
        { prefix: 'qcOutRewards',    endpoint: '/api/qc/outgoing-rewards',    name: 'Reward Assignment',    cardId: 'qcOutCard_rewards',   outgoing: true },
        { prefix: 'qcOutPebblepost', endpoint: '/api/qc/outgoing-pebblepost', name: 'Pebble Post',          cardId: 'qcOutCard_pebblepost', outgoing: true },
        { prefix: 'qcOutGaHourly',   endpoint: '/api/qc/outgoing-ga-hourly',  name: 'GA Hourly → Bluecore', cardId: 'qcOutCard_ga',        outgoing: true, type: 'ga-hourly' },
        { prefix: 'qcOutCriteo',     endpoint: '/api/qc/outgoing-criteo',     name: 'Criteo',               cardId: 'qcOutCard_criteo',    outgoing: true },
        { prefix: 'qcVendorCdi',   endpoint: '/api/qc/vendor-cdi',         name: 'CDI → Experian Exchange', cardId: 'qcVendorCard_cdi',   vendor: true, vendorType: 'cdi' },
        { prefix: 'qcVendorBrite',  endpoint: '/api/qc/vendor-briteverify', name: 'BriteVerify Email Validation', cardId: 'qcVendorCard_brite',  vendor: true, vendorType: 'brite' },
        { prefix: 'qcVendorOracle', endpoint: '/api/qc/vendor-oracle',       name: 'Oracle Sync to Redshift',      cardId: 'qcVendorCard_oracle', vendor: true, vendorType: 'oracle' },
    ];

    let qcLastRunResults = [];

    function renderQcPanel(prefix, data) {
        const checkDateEl = document.getElementById(prefix + 'CheckDate');
        const summaryEl = document.getElementById(prefix + 'Summary');
        const containerEl = document.getElementById(prefix + 'ResultsContainer');
        const tbodyEl = document.getElementById(prefix + 'TableBody');
        const hintEl = document.getElementById(prefix + 'PendingHint');

        if (checkDateEl) checkDateEl.textContent = data.check_date;

        summaryEl.className = 'qc-summary ' + (data.issue_count > 0 ? 'qc-summary-issues' : 'qc-summary-ok');
        summaryEl.textContent = data.issue_count > 0
            ? `${data.issue_count} issue${data.issue_count !== 1 ? 's' : ''} found for ${data.check_date}`
            : `All files OK for ${data.check_date}`;
        summaryEl.style.display = 'block';

        tbodyEl.innerHTML = '';
        data.rows.forEach(row => {
            const tr = document.createElement('tr');
            if (row.status !== 'ok') tr.className = `qc-row-${row.status}`;

            const sizeDisplay = (row.size !== null && row.size !== undefined) ? formatBytes(row.size) : '—';
            const countDisplay = (row.count !== null && row.count !== undefined) ? row.count.toLocaleString() : '—';

            const tdName = document.createElement('td');
            tdName.textContent = row.name;
            const tdSize = document.createElement('td');
            tdSize.textContent = sizeDisplay;
            const tdCount = document.createElement('td');
            tdCount.textContent = countDisplay;
            const tdStatus = document.createElement('td');
            const pill = document.createElement('span');
            pill.className = `qc-pill qc-pill-${row.status}`;
            if (row.status === 'low') {
                pill.textContent = `LOW COUNT · min: ${(row.min || 0).toLocaleString()}`;
            } else if (row.status === 'high') {
                pill.textContent = `HIGH COUNT · max: ${(row.max || 0).toLocaleString()}`;
            } else {
                pill.textContent = QC_PILL_LABELS[row.status] || row.status.toUpperCase();
            }
            tdStatus.appendChild(pill);

            tr.appendChild(tdName);
            tr.appendChild(tdSize);
            tr.appendChild(tdCount);
            tr.appendChild(tdStatus);
            tbodyEl.appendChild(tr);
        });

        if (hintEl) hintEl.style.display = 'none';
        containerEl.style.display = 'block';
    }

    // ==================== OUTGOING CARD RENDERERS ====================
    function _outgoingCardHtml(feed, statusClass, badgeClass, badgeText, bodyHtml) {
        return `
            <div class="qc-out-card-header">
                <span class="qc-out-feed-name">${feed.name}</span>
                <span class="qc-out-badge ${badgeClass}">${badgeText}</span>
            </div>
            <div class="qc-out-card-body">${bodyHtml}</div>
            <div class="qc-out-card-bar qc-out-bar-${statusClass}"></div>`;
    }

    function renderOutgoingCard(feed, data) {
        const card = document.getElementById(feed.cardId);
        if (!card) return;

        const hasIssues = data.issue_count > 0;
        const allMissing = data.rows.length > 0 && data.rows.every(r => r.status === 'missing');
        const statusClass = hasIssues ? 'fail' : 'pass';
        const badgeText   = allMissing ? 'MISSING' : (hasIssues ? 'FAIL' : 'PASS');
        const badgeClass  = hasIssues ? 'qc-out-badge-fail' : 'qc-out-badge-pass';

        let bodyHtml = '';
        if (data.rows.length === 1) {
            const row = data.rows[0];
            bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">File</span><span class="qc-out-stat-value qc-out-fname">${row.name}</span></div>`;
            bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Records sent</span><span class="qc-out-stat-value">${row.count !== null ? row.count.toLocaleString() : '&mdash;'}</span></div>`;
            if (row.status === 'low') {
                bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Min threshold</span><span class="qc-out-stat-value">${row.min.toLocaleString()}</span></div>`;
                bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Reason</span><span class="qc-out-stat-value">Below minimum count</span></div>`;
            } else if (row.status === 'high') {
                bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Max threshold</span><span class="qc-out-stat-value">${row.max.toLocaleString()}</span></div>`;
                bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Reason</span><span class="qc-out-stat-value">Above maximum count</span></div>`;
            } else if (row.status === 'missing') {
                bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Reason</span><span class="qc-out-stat-value">File not found on server</span></div>`;
            }
            bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Sent at</span><span class="qc-out-stat-value">${row.modified || '&mdash;'}</span></div>`;
        } else {
            const okCount = data.rows.filter(r => r.status === 'ok').length;
            bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Files</span><span class="qc-out-stat-value">${okCount} of ${data.rows.length} OK</span></div>`;
            data.rows.forEach(row => {
                const isIssue = row.status !== 'ok';
                bodyHtml += `<div class="qc-out-file-item${isIssue ? ' qc-out-file-item-issue' : ''}">
                    <span class="qc-out-file-dot ${isIssue ? 'qc-out-dot-fail' : 'qc-out-dot-pass'}"></span>
                    <span class="qc-out-file-label">${row.name}</span>
                    <span class="qc-out-file-count">${row.count !== null ? row.count.toLocaleString() : '&mdash;'}</span>
                </div>`;
                if (row.status === 'low')     bodyHtml += `<div class="qc-out-file-reason">Below min: ${row.min.toLocaleString()}</div>`;
                else if (row.status === 'high')    bodyHtml += `<div class="qc-out-file-reason">Above max: ${row.max.toLocaleString()}</div>`;
                else if (row.status === 'missing') bodyHtml += `<div class="qc-out-file-reason">File not found</div>`;
            });
            const latestMod = data.rows.map(r => r.modified).find(m => m);
            if (latestMod) bodyHtml += `<div class="qc-out-stat-row" style="margin-top:0.5rem;"><span class="qc-out-stat-label">Sent at</span><span class="qc-out-stat-value">${latestMod}</span></div>`;
        }

        card.className = `qc-out-card qc-out-card-${statusClass}`;
        card.innerHTML = _outgoingCardHtml(feed, statusClass, badgeClass, badgeText, bodyHtml);
    }

    function renderGaHourlyCard(feed, data) {
        const card = document.getElementById(feed.cardId);
        if (!card) return;
        const hasFiles   = data.file_count > 0;
        const statusClass = hasFiles ? 'pass' : 'fail';
        const badgeClass  = hasFiles ? 'qc-out-badge-pass' : 'qc-out-badge-fail';
        const badgeText   = hasFiles ? 'PASS' : 'MISSING';
        let bodyHtml = `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Files found today</span><span class="qc-out-stat-value">${data.file_count}</span></div>`;
        if (!hasFiles) {
            bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Reason</span><span class="qc-out-stat-value">No hourly files for today</span></div>`;
        } else {
            if (data.latest_modified) bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Latest sent at</span><span class="qc-out-stat-value">${data.latest_modified}</span></div>`;
            const latest = data.files[data.files.length - 1];
            if (latest) bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Latest file</span><span class="qc-out-stat-value qc-out-fname">${latest}</span></div>`;
        }
        card.className = `qc-out-card qc-out-card-${statusClass}`;
        card.innerHTML = _outgoingCardHtml(feed, statusClass, badgeClass, badgeText, bodyHtml);
    }

    // ==================== VENDOR CARD RENDERERS ====================
    function renderVendorCdiCard(feed, data) {
        const card = document.getElementById(feed.cardId);
        if (!card) return;
        const hasIssues = data.issue_count > 0;
        const statusClass = hasIssues ? 'fail' : 'pass';
        const badgeClass  = hasIssues ? 'qc-out-badge-fail' : 'qc-out-badge-pass';
        const badgeText   = hasIssues ? 'FAIL' : 'PASS';
        const sent = data.sent;
        const ret  = data.return;

        let bodyHtml = '<div class="qc-vendor-section-label">SENT FILE</div>';
        bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">File</span><span class="qc-out-stat-value qc-out-fname">${sent.name}</span></div>`;
        bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Records sent</span><span class="qc-out-stat-value">${sent.count !== null ? sent.count.toLocaleString() : '&mdash;'}</span></div>`;
        if (sent.status === 'low') {
            bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Min threshold</span><span class="qc-out-stat-value">${sent.min.toLocaleString()}</span></div>`;
            bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Reason</span><span class="qc-out-stat-value">Below minimum count</span></div>`;
        } else if (sent.status === 'high') {
            bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Max threshold</span><span class="qc-out-stat-value">${sent.max.toLocaleString()}</span></div>`;
            bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Reason</span><span class="qc-out-stat-value">Above maximum count</span></div>`;
        } else if (sent.status === 'missing') {
            bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Reason</span><span class="qc-out-stat-value">File not found on server</span></div>`;
        }
        bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Sent at</span><span class="qc-out-stat-value">${sent.modified || '&mdash;'}</span></div>`;

        bodyHtml += '<div class="qc-vendor-section-label">RETURN FILE</div>';
        bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">File</span><span class="qc-out-stat-value qc-out-fname">${ret.name}</span></div>`;
        bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Records received</span><span class="qc-out-stat-value">${ret.count !== null ? ret.count.toLocaleString() : '&mdash;'}</span></div>`;
        if (ret.status === 'high') {
            bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Max allowed</span><span class="qc-out-stat-value">${ret.sent_count !== null ? ret.sent_count.toLocaleString() : '&mdash;'} (= sent count)</span></div>`;
            bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Reason</span><span class="qc-out-stat-value">Return count exceeds sent count</span></div>`;
        } else if (ret.status === 'low') {
            const minRet = ret.min_return !== null ? ret.min_return.toLocaleString() : '&mdash;';
            bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Min allowed</span><span class="qc-out-stat-value">${minRet} (90% of sent)</span></div>`;
            bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Reason</span><span class="qc-out-stat-value">Return dropped more than 10% of sent</span></div>`;
        } else if (ret.status === 'missing') {
            bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Reason</span><span class="qc-out-stat-value">Return file not found on server</span></div>`;
        } else if (ret.count !== null && ret.sent_count !== null) {
            const minRet = ret.min_return !== null ? ret.min_return.toLocaleString() : '&mdash;';
            bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Allowed range</span><span class="qc-out-stat-value">${minRet} – ${ret.sent_count.toLocaleString()}</span></div>`;
        }
        bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Received at</span><span class="qc-out-stat-value">${ret.modified || '&mdash;'}</span></div>`;

        card.className = `qc-out-card qc-out-card-${statusClass}`;
        card.innerHTML = _outgoingCardHtml(feed, statusClass, badgeClass, badgeText, bodyHtml);
    }

    function renderVendorBriteCard(feed, data) {
        const card = document.getElementById(feed.cardId);
        if (!card) return;
        const hasIssues  = data.issue_count > 0;
        const statusClass = hasIssues ? 'fail' : 'pass';
        const badgeClass  = hasIssues ? 'qc-out-badge-fail' : 'qc-out-badge-pass';
        const badgeText   = hasIssues ? 'MISSING' : 'PASS';
        const f = data.file;
        let bodyHtml = `<div class="qc-out-stat-row"><span class="qc-out-stat-label">File</span><span class="qc-out-stat-value qc-out-fname">${f.name}</span></div>`;
        if (f.status === 'missing') {
            bodyHtml += `<div class="qc-out-stat-row qc-out-stat-issue"><span class="qc-out-stat-label">Reason</span><span class="qc-out-stat-value">File not found on server</span></div>`;
        } else {
            bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Records</span><span class="qc-out-stat-value">${f.count !== null ? f.count.toLocaleString() : '&mdash;'}</span></div>`;
            bodyHtml += `<div class="qc-out-stat-row"><span class="qc-out-stat-label">Received at</span><span class="qc-out-stat-value">${f.modified || '&mdash;'}</span></div>`;
        }
        card.className = `qc-out-card qc-out-card-${statusClass}`;
        card.innerHTML = _outgoingCardHtml(feed, statusClass, badgeClass, badgeText, bodyHtml);
    }

    function renderVendorOracleCard(feed, data) {
        const card = document.getElementById(feed.cardId);
        if (!card) return;
        const hasIssues  = data.issue_count > 0;
        const statusClass = hasIssues ? 'fail' : 'pass';
        const badgeClass  = hasIssues ? 'qc-out-badge-fail' : 'qc-out-badge-pass';
        const badgeText   = hasIssues ? 'MISSING' : 'PASS';
        let bodyHtml = '';
        data.rows.forEach(row => {
            const isOk = row.status === 'ok';
            bodyHtml += `<div class="qc-out-file-item${isOk ? '' : ' qc-out-file-item-issue'}">
                <span class="qc-out-file-dot ${isOk ? 'qc-out-dot-pass' : 'qc-out-dot-fail'}"></span>
                <span class="qc-out-file-label">${row.name}</span>
                <span class="qc-out-file-count">${isOk ? (row.modified || '') : 'MISSING'}</span>
            </div>`;
        });
        card.className = `qc-out-card qc-out-card-${statusClass}`;
        card.innerHTML = _outgoingCardHtml(feed, statusClass, badgeClass, badgeText, bodyHtml);
    }

    function renderOutgoingCardError(feed, msg) {
        const card = document.getElementById(feed.cardId);
        if (!card) return;
        card.className = 'qc-out-card qc-out-card-fail';
        card.innerHTML = _outgoingCardHtml(feed, 'fail', 'qc-out-badge-fail', 'ERROR',
            `<p class="qc-out-error-msg">${msg}</p>`);
    }

    async function runQcFeed(feed) {
        const { prefix, endpoint, name } = feed;

        if (feed.vendor) {
            try {
                const response = await fetch(endpoint);
                const data = await response.json();
                if (data.error) {
                    renderOutgoingCardError(feed, data.error);
                    return { ok: false, issue_count: 0, name, data: null };
                }
                if (feed.vendorType === 'cdi') renderVendorCdiCard(feed, data);
                else if (feed.vendorType === 'oracle') renderVendorOracleCard(feed, data);
                else renderVendorBriteCard(feed, data);
                return { ok: true, issue_count: data.issue_count, name, data };
            } catch (err) {
                renderOutgoingCardError(feed, err.message);
                return { ok: false, issue_count: 0, name, data: null };
            }
        }

        if (feed.outgoing) {
            try {
                const response = await fetch(endpoint);
                const data = await response.json();
                if (data.error) {
                    renderOutgoingCardError(feed, data.error);
                    return { ok: false, issue_count: 0, name, data: null };
                }
                if (feed.type === 'ga-hourly') {
                    renderGaHourlyCard(feed, data);
                    const ga_issues = data.file_count > 0 ? 0 : 1;
                    return { ok: true, issue_count: ga_issues, name, data: { rows: [], issue_count: ga_issues, check_date: data.check_date } };
                }
                renderOutgoingCard(feed, data);
                return { ok: true, issue_count: data.issue_count, name, data };
            } catch (err) {
                renderOutgoingCardError(feed, err.message);
                return { ok: false, issue_count: 0, name, data: null };
            }
        }

        // Incoming feeds: prefix-based DOM elements
        const errorEl     = document.getElementById(prefix + 'Error');
        const summaryEl   = document.getElementById(prefix + 'Summary');
        const containerEl = document.getElementById(prefix + 'ResultsContainer');
        errorEl.style.display    = 'none';
        summaryEl.style.display  = 'none';
        containerEl.style.display = 'none';

        try {
            const response = await fetch(endpoint);
            const data = await response.json();
            if (data.error) {
                errorEl.textContent = data.error;
                errorEl.style.display = 'block';
                return { ok: false, issue_count: 0, name, data: null };
            }
            renderQcPanel(prefix, data);
            return { ok: true, issue_count: data.issue_count, name, data };
        } catch (error) {
            errorEl.textContent = 'Error running check: ' + error.message;
            errorEl.style.display = 'block';
            return { ok: false, issue_count: 0, name, data: null };
        }
    }

    // ==================== EA TODAY EVENT ====================
    const EVENT_LABELS = {
        campaign_start_date:       'Campaign Start',
        campaign_end_date:         'Campaign End',
        campaign_drop_date:        'Campaign Drop',
        cdi_full_refresh_send:     'CDI Full Refresh Send',
        cdi_full_refresh_receive:  'CDI Full Refresh Receive',
        modeling_kick_off:         'Modeling Kick-off',
        final_model_due:           'Final Model Due',
        lp_score_approval:         'LP Score Approval',
        final_scoring:             'Final Scoring',
        lp_count_approval:         'LP Count Approval',
        ea_merge_purge_delivery:   'EA Merge/Purge Delivery',
        cumm_cell:                 'Cumm Cell',
        circ_plan:                 'Circ Plan',
        mail_file:                 'Mail File',
        hygine_files:              'Hygiene Files',
        ntf_estimate_rank_score:   'NTF Estimate Rank Score',
        ntf_actual_rank_score:     'NTF Actual Rank Score',
        ea_ntf_merge_purge_delvr:  'EA NTF Merge/Purge Delivery',
        ntf_mail_file:             'NTF Mail File',
        ntf_hygine_files:          'NTF Hygiene Files',
        ntf_cumm_cell:             'NTF Cumm Cell',
        gross_in:                  'Gross In',
        campaign_ntf_start_date:   'Campaign NTF Start',
    };

    const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

    function formatEventDate(iso) {
        const [y, m, d] = iso.split('-').map(Number);
        const dt = new Date(y, m - 1, d);
        return `${DAY_NAMES[dt.getDay()]} ${MONTH_NAMES[m-1]} ${d}`;
    }

    function formatWeekBanner(startIso, endIso) {
        const [sy, sm, sd] = startIso.split('-').map(Number);
        const [ey, em, ed] = endIso.split('-').map(Number);
        return `${MONTH_NAMES[sm-1]} ${sd} – ${MONTH_NAMES[em-1]} ${ed}, ${ey}`;
    }

    let teLoaded = false;

    async function loadTodayEvents() {
        const loadingEl = document.getElementById('teLoading');
        const errorEl   = document.getElementById('teError');
        const contentEl = document.getElementById('teContent');
        const weekEl    = document.getElementById('teWeekLabel');
        const emptyEl   = document.getElementById('teEmpty');
        const tbody     = document.getElementById('teTableBody');

        loadingEl.style.display = 'flex';
        errorEl.style.display   = 'none';
        contentEl.style.display = 'none';

        try {
            const resp = await fetch('/api/today-events');
            const data = await resp.json();
            if (data.error) {
                errorEl.textContent   = data.error;
                errorEl.style.display = 'block';
                return;
            }

            weekEl.textContent = 'Week of ' + formatWeekBanner(data.week_start, data.week_end);

            tbody.innerHTML = '';
            if (!data.events || data.events.length === 0) {
                emptyEl.style.display = 'block';
            } else {
                emptyEl.style.display = 'none';
                let lastDate = null;
                data.events.forEach(ev => {
                    if (ev.event_date !== lastDate) {
                        lastDate = ev.event_date;
                        const dateRow = document.createElement('tr');
                        dateRow.className = 'te-date-row' + (ev.is_today ? ' te-date-today' : '');
                        dateRow.innerHTML = `<td colspan="3" class="te-date-cell">${formatEventDate(ev.event_date)}${ev.is_today ? ' <span class="te-today-chip">TODAY</span>' : ''}</td>`;
                        tbody.appendChild(dateRow);
                    }
                    const row = document.createElement('tr');
                    row.className = 'te-event-row' + (ev.is_today ? ' te-event-today' : '');
                    row.innerHTML = `
                        <td class="te-col-date"></td>
                        <td class="te-col-event">${EVENT_LABELS[ev.event_type] || ev.event_type}</td>
                        <td class="te-col-campaign">${ev.campaign_name}</td>`;
                    tbody.appendChild(row);
                });
            }

            contentEl.style.display = 'block';
            teLoaded = true;
        } catch(err) {
            errorEl.textContent   = 'Failed to load: ' + err.message;
            errorEl.style.display = 'block';
        } finally {
            loadingEl.style.display = 'none';
        }
    }

    // Auto-load when subtab clicked; refresh button
    document.querySelectorAll('.subtab-button[data-subtab="qc-today-event"]').forEach(btn => {
        btn.addEventListener('click', () => { if (!teLoaded) loadTodayEvents(); });
    });
    const teRefreshBtn = document.getElementById('teRefreshBtn');
    if (teRefreshBtn) teRefreshBtn.addEventListener('click', () => { teLoaded = false; loadTodayEvents(); });

    const qcRunAllBtn = document.getElementById('qcRunAllBtn');
    const qcRunAllText = document.getElementById('qcRunAllText');
    const qcRunAllLoader = document.getElementById('qcRunAllLoader');
    const qcRunAllSummary = document.getElementById('qcRunAllSummary');
    const qcLastRunAt = document.getElementById('qcLastRunAt');

    // ==================== QC LOADING PROGRESS MODAL ====================
    function showQcLoadingModal() {
        const list = document.getElementById('qcProgressList');
        list.innerHTML = QC_FEEDS.map((feed, i) => `
            <div class="qc-progress-item">
                <span class="qc-progress-dot qc-dot-pending"></span>
                <span class="qc-progress-feed-name">${feed.name}</span>
                <span class="qc-progress-status qc-status-pending" id="qcProg_${i}">Pending</span>
            </div>
        `).join('');
        document.getElementById('qcLoadingModal').style.display = 'flex';
    }

    function updateQcProgress(index, state) {
        const el = document.getElementById(`qcProg_${index}`);
        const item = el ? el.closest('.qc-progress-item') : null;
        if (!el || !item) return;
        const dot = item.querySelector('.qc-progress-dot');

        dot.className = 'qc-progress-dot';
        el.className = 'qc-progress-status';

        if (state === 'loading') {
            dot.classList.add('qc-dot-loading');
            el.classList.add('qc-status-loading');
            el.textContent = 'Loading...';
        } else {
            dot.classList.add('qc-dot-ok');
            el.classList.add('qc-status-ok');
            el.textContent = 'Completed';
        }
    }

    function closeQcLoadingModal() {
        document.getElementById('qcLoadingModal').style.display = 'none';
    }

    if (qcRunAllBtn) {
        qcRunAllBtn.addEventListener('click', async function () {
            qcRunAllText.textContent = 'Running...';
            qcRunAllLoader.style.display = 'inline-block';
            qcRunAllBtn.disabled = true;
            qcRunAllSummary.style.display = 'none';

            showQcLoadingModal();

            qcLastRunResults = await Promise.all(QC_FEEDS.map((feed, index) => {
                updateQcProgress(index, 'loading');
                return runQcFeed(feed).then(result => {
                    updateQcProgress(index, 'done');
                    return result;
                });
            }));

            closeQcLoadingModal();

            const totalIssues = qcLastRunResults.reduce((sum, r) => sum + (r.issue_count || 0), 0);
            const okCount = qcLastRunResults.filter(r => r.ok).length;
            const hasIssues = totalIssues > 0;

            qcRunAllSummary.className = 'qc-summary qc-summary-clickable ' + (hasIssues ? 'qc-summary-issues' : 'qc-summary-ok');
            const summaryText = hasIssues
                ? `${totalIssues} total issue${totalIssues !== 1 ? 's' : ''} found across ${okCount} feed${okCount !== 1 ? 's' : ''} checked`
                : `All feeds OK (${okCount} feed${okCount !== 1 ? 's' : ''} checked)`;
            qcRunAllSummary.innerHTML = `<span>${summaryText}</span><span class="qc-summary-view-link">View Summary &rarr;</span>`;
            qcRunAllSummary.style.display = 'flex';

            qcLastRunAt.textContent = 'Last run: ' + new Date().toLocaleTimeString();
            qcRunAllText.textContent = 'Run Daily QC Check';
            qcRunAllLoader.style.display = 'none';
            qcRunAllBtn.disabled = false;
        });

        qcRunAllSummary.addEventListener('click', function () {
            if (qcLastRunResults.length === 0) return;
            openQcSummaryModal();
        });
    }

    // ==================== QC SUMMARY MODAL ====================
    function openQcSummaryModal() {
        const checkDate = qcLastRunResults.find(r => r.data)?.data.check_date || '—';
        document.getElementById('qcSummaryModalDate').textContent = 'Check date: ' + checkDate;
        document.getElementById('qcSummaryEmailResult').style.display = 'none';

        let html = '';
        qcLastRunResults.forEach(r => {
            if (!r.ok || !r.data) {
                html += `<div class="qc-modal-feed">
                    <div class="qc-modal-feed-header">
                        <span class="qc-modal-feed-name">${r.name}</span>
                        <span class="qc-pill qc-pill-missing">ERROR</span>
                    </div>
                </div>`;
            } else if (r.issue_count === 0) {
                html += `<div class="qc-modal-feed qc-modal-feed-ok">
                    <div class="qc-modal-feed-header">
                        <span class="qc-modal-feed-name">${r.name}</span>
                        <span class="qc-pill qc-pill-ok">ALL OK</span>
                    </div>
                </div>`;
            } else {
                const issues = (r.data.rows || []).filter(row => row.status !== 'ok');
                html += `<div class="qc-modal-feed qc-modal-feed-issues">
                    <div class="qc-modal-feed-header">
                        <span class="qc-modal-feed-name">${r.name}</span>
                        <span class="qc-pill qc-pill-missing">${r.issue_count} issue${r.issue_count !== 1 ? 's' : ''}</span>
                    </div>
                    <table class="qc-modal-issue-table">
                        <thead><tr><th>File</th><th>Status</th><th>Row Count</th></tr></thead>
                        <tbody>${issues.map(row => `
                            <tr>
                                <td class="qc-modal-filename">${row.name}</td>
                                <td><span class="qc-pill qc-pill-${row.status}">${QC_PILL_LABELS[row.status] || row.status}</span></td>
                                <td>${(row.count !== null && row.count !== undefined) ? row.count : '&mdash;'}</td>
                            </tr>`).join('')}
                        </tbody>
                    </table>
                </div>`;
            }
        });
        document.getElementById('qcSummaryModalContent').innerHTML = html;
        document.getElementById('qcSummaryModal').style.display = 'flex';
    }

    const qcSummaryModal    = document.getElementById('qcSummaryModal');
    const qcSummaryModalClose = document.getElementById('qcSummaryModalClose');
    const qcSummaryCloseBtn = document.getElementById('qcSummaryCloseBtn');
    const qcSendEmailBtn    = document.getElementById('qcSendEmailBtn');
    const qcSendEmailText   = document.getElementById('qcSendEmailText');
    const qcSendEmailLoader = document.getElementById('qcSendEmailLoader');
    const qcSummaryEmailResult = document.getElementById('qcSummaryEmailResult');

    function closeQcSummaryModal() { qcSummaryModal.style.display = 'none'; }
    qcSummaryModalClose.addEventListener('click', closeQcSummaryModal);
    qcSummaryCloseBtn.addEventListener('click', closeQcSummaryModal);
    qcSummaryModal.addEventListener('click', e => { if (e.target === qcSummaryModal) closeQcSummaryModal(); });

    qcSendEmailBtn.addEventListener('click', async function () {
        const checkDate = qcLastRunResults.find(r => r.data)?.data.check_date || '—';
        const totalIssues = qcLastRunResults.reduce((sum, r) => sum + (r.issue_count || 0), 0);
        const feedsPayload = qcLastRunResults.map(r => ({
            name: r.name,
            issue_count: r.issue_count || 0,
            issues: r.data ? r.data.rows.filter(row => row.status !== 'ok').map(row => ({
                name: row.name, status: row.status, count: row.count
            })) : []
        }));

        qcSendEmailText.textContent = 'Sending...';
        qcSendEmailLoader.style.display = 'inline-block';
        qcSendEmailBtn.disabled = true;

        try {
            const res = await fetch('/api/qc/send-summary-email', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ check_date: checkDate, total_issues: totalIssues, feeds: feedsPayload })
            });
            const data = await res.json();
            qcSummaryEmailResult.style.display = 'block';
            if (data.error) {
                qcSummaryEmailResult.className = 'qc-email-result qc-email-result-error';
                qcSummaryEmailResult.textContent = 'Failed: ' + data.error;
            } else {
                qcSummaryEmailResult.className = 'qc-email-result qc-email-result-ok';
                qcSummaryEmailResult.textContent = 'Email sent to ' + data.to;
            }
        } catch (err) {
            qcSummaryEmailResult.style.display = 'block';
            qcSummaryEmailResult.className = 'qc-email-result qc-email-result-error';
            qcSummaryEmailResult.textContent = 'Error: ' + err.message;
        } finally {
            qcSendEmailText.textContent = 'Send Email to LP';
            qcSendEmailLoader.style.display = 'none';
            qcSendEmailBtn.disabled = false;
        }
    });

});
