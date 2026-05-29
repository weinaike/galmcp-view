/* === Galaxy Fitting Label Tool - Client-side JS === */

// --- Log Modal ---

function openLogModal(source, galaxyId, timestampDir) {
    var modal = document.getElementById('log-modal');
    var body = document.getElementById('log-body');
    var title = document.getElementById('log-modal-title');
    if (!modal || !body) return;
    title.textContent = '日志 — ' + galaxyId + ' / ' + timestampDir;
    body.textContent = '加载中…';
    modal.classList.add('active');

    fetch('/summary/' + source + '/' + galaxyId + '/' + timestampDir)
        .then(function(resp) { return resp.text(); })
        .then(function(text) { body.textContent = text; })
        .catch(function() { body.textContent = '加载日志失败'; });
}

function closeLogModal(e) {
    var modal = document.getElementById('log-modal');
    if (!modal) return;
    if (e && e.target !== modal && !e.target.classList.contains('modal-close')) return;
    modal.classList.remove('active');
}

// --- Component Analysis Modal ---

function openCompAnalysisModal(source, galaxyId, timestampDir) {
    var modal = document.getElementById('comp-modal');
    var body = document.getElementById('comp-body');
    var title = document.getElementById('comp-modal-title');
    if (!modal || !body) return;
    title.textContent = '成分分析 — ' + galaxyId + ' / ' + timestampDir;
    body.innerHTML = '<p style="color:var(--text-muted)">加载中…</p>';
    modal.classList.add('active');

    fetch('/component-analysis/' + source + '/' + galaxyId + '/' + timestampDir)
        .then(function(resp) { return resp.text(); })
        .then(function(html) { body.innerHTML = html; })
        .catch(function() { body.innerHTML = '<p style="color:var(--red)">加载成分分析失败</p>'; });
}

function closeCompAnalysisModal(e) {
    var modal = document.getElementById('comp-modal');
    if (!modal) return;
    if (e && e.target !== modal && !e.target.classList.contains('modal-close')) return;
    modal.classList.remove('active');
}

// Toggle form sections based on accept/reject selection
function onPerfectChange() {
    var isYes = document.getElementById('perfect-yes').checked;
    var bestRoundGroup = document.getElementById('best-round-group');
    var reasonGroup = document.getElementById('reason-group');
    var commentsGroup = document.getElementById('comments-group');

    if (isYes) {
        bestRoundGroup.style.display = 'block';
        reasonGroup.style.display = 'block';
        commentsGroup.style.display = 'none';
    } else {
        bestRoundGroup.style.display = 'none';
        reasonGroup.style.display = 'none';
        commentsGroup.style.display = 'block';
    }
}

// Highlight selected round card when best_round changes
document.addEventListener('DOMContentLoaded', function() {
    var selectEl = document.getElementById('best-round');
    if (selectEl) {
        selectEl.addEventListener('change', function() {
            // Remove highlight from all rounds
            document.querySelectorAll('.round-card').forEach(function(card) {
                card.classList.remove('selected');
            });
            // Add highlight to selected round
            var roundNum = selectEl.value;
            if (roundNum) {
                var target = document.getElementById('round-' + roundNum);
                if (target) {
                    target.classList.add('selected');
                }
            }
        });

        // Initial highlight if already selected
        if (selectEl.value) {
            var target = document.getElementById('round-' + selectEl.value);
            if (target) {
                target.classList.add('selected');
            }
        }
    }

    // Initialize form state if a radio is already checked
    var yesRadio = document.getElementById('perfect-yes');
    var noRadio = document.getElementById('perfect-no');
    if (yesRadio && noRadio) {
        if (yesRadio.checked || noRadio.checked) {
            onPerfectChange();
        }
    }
});

// Filter sample list
function filterSamples(type) {
    var rows = document.querySelectorAll('.sample-row');
    var buttons = document.querySelectorAll('.filter-btn');

    buttons.forEach(function(btn) { btn.classList.remove('active'); });
    event.target.classList.add('active');

    rows.forEach(function(row) {
        var evaluated = row.dataset.evaluated === 'true';
        if (type === 'all') {
            row.style.display = '';
        } else if (type === 'evaluated') {
            row.style.display = evaluated ? '' : 'none';
        } else if (type === 'pending') {
            row.style.display = evaluated ? 'none' : '';
        }
    });
}

// --- Analysis Report Modal ---

function openReportModal(source, galaxyId) {
    var modal = document.getElementById('report-modal');
    var body = document.getElementById('report-body');
    if (!modal || !body) return;
    body.innerHTML = '<p style="color:var(--text-muted)">加载中…</p>';
    modal.classList.add('active');

    fetch('/analysis-report/' + source + '/' + galaxyId)
        .then(function(resp) { return resp.text(); })
        .then(function(html) { body.innerHTML = html; })
        .catch(function() { body.innerHTML = '<p style="color:var(--red)">加载报告失败</p>'; });
}

function closeReportModal(e) {
    var modal = document.getElementById('report-modal');
    if (!modal) return;
    if (e && e.target !== modal && !e.target.classList.contains('modal-close')) return;
    modal.classList.remove('active');
}

// --- Image Lightbox ---

(function() {
    var overlay = document.getElementById('lightbox-overlay');
    var lbImg = document.getElementById('lightbox-img');
    if (!overlay || !lbImg) return;

    var scale = 1;
    var tx = 0, ty = 0;
    var dragging = false, dragStartX = 0, dragStartY = 0, startTx = 0, startTy = 0;

    function applyTransform() {
        lbImg.style.transform = 'translate(' + tx + 'px, ' + ty + 'px) scale(' + scale + ')';
    }

    function resetAndClose() {
        overlay.classList.remove('active');
        scale = 1; tx = 0; ty = 0;
        lbImg.style.transform = '';
    }

    function openLightbox(src) {
        lbImg.src = src;
        scale = 1; tx = 0; ty = 0;
        lbImg.style.transform = '';
        overlay.classList.add('active');
    }

    // Bind to all detail-page images
    document.addEventListener('DOMContentLoaded', function() {
        document.querySelectorAll('.round-image img, .eval-image, .s4g-section .round-image img').forEach(function(img) {
            img.style.cursor = 'zoom-in';
            img.addEventListener('click', function(e) {
                e.stopPropagation();
                openLightbox(img.src);
            });
        });
    });

    // Click overlay (not image) to close
    overlay.addEventListener('click', function(e) {
        if (e.target === overlay) resetAndClose();
    });

    // Escape to close
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && overlay.classList.contains('active')) resetAndClose();
    });

    // Scroll to zoom
    overlay.addEventListener('wheel', function(e) {
        if (!overlay.classList.contains('active')) return;
        e.preventDefault();
        var delta = e.deltaY > 0 ? -0.15 : 0.15;
        scale = Math.max(0.5, Math.min(5.0, scale + delta));
        applyTransform();
    }, { passive: false });

    // Drag to pan
    lbImg.addEventListener('mousedown', function(e) {
        if (scale <= 1) return;
        e.preventDefault();
        dragging = true;
        dragStartX = e.clientX;
        dragStartY = e.clientY;
        startTx = tx;
        startTy = ty;
        lbImg.style.cursor = 'grabbing';
    });
    document.addEventListener('mousemove', function(e) {
        if (!dragging) return;
        tx = startTx + (e.clientX - dragStartX);
        ty = startTy + (e.clientY - dragStartY);
        applyTransform();
    });
    document.addEventListener('mouseup', function() {
        if (!dragging) return;
        dragging = false;
        lbImg.style.cursor = 'default';
    });
})();
