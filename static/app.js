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

// --- Working Note Modal ---

function openWorkingNoteModal(source, galaxyId) {
    var modal = document.getElementById('working-note-modal');
    var body = document.getElementById('working-note-body');
    if (!modal || !body) return;
    body.innerHTML = '<p style="color:var(--text-muted)">加载中…</p>';
    modal.classList.add('active');

    fetch('/working-note/' + source + '/' + galaxyId)
        .then(function(resp) { return resp.text(); })
        .then(function(html) { body.innerHTML = html; })
        .catch(function() { body.innerHTML = '<p style="color:var(--red)">加载失败</p>'; });
}

function closeWorkingNoteModal(e) {
    var modal = document.getElementById('working-note-modal');
    if (!modal) return;
    if (e && e.target !== modal && !e.target.classList.contains('modal-close')) return;
    modal.classList.remove('active');
}

// === visualRAG KB inline panel / review-modal (AJAX panel fragments) ===
// The /kb/ajax/* routes return the same kb_panel HTML fragment (rendered by the
// _kb_editor macro). Each surface that hosts a panel is a [data-kb-root]
// container: a per-round inline slot (#kb-round-<n>, beside that round's image)
// on the detail page, or the kb_review modal body (#kb-modal-body).
//
// Targeting is DOM-RELATIVE, not global: every action finds its own host via
// closest('[data-kb-root]'), so multiple rounds can be open at once without
// cross-talk. The × button passes `this` so kbClose knows which slot to clear.
(function () {
    var dirty = false;  // mutation happened inside the review modal -> refresh on close

    function enc(s) { return encodeURIComponent(s); }
    function rootOf(el) { return el && el.closest ? el.closest('[data-kb-root]') : null; }
    // auto-grow a textarea to its content (no fixed height, no manual resize)
    function autosize(el) { el.style.height = 'auto'; el.style.height = (el.scrollHeight + 2) + 'px'; }
    function autosizeAll(root) { if (root) root.querySelectorAll('.kb-textarea').forEach(autosize); }
    function swapInto(root, html) { if (!root) return; root.innerHTML = html; autosizeAll(root); }
    // grow/shrink the textarea live as the expert edits (delegated, once)
    document.addEventListener('input', function (ev) {
        var t = ev.target;
        if (t && t.classList && t.classList.contains('kb-textarea')) autosize(t);
    });
    function placeholder() {
        return '<div class="kb-empty-inline"><p>点标题栏 <b>&#129514; 蒸馏/查看</b> 或 <b>&#128269; 成分分析</b> 在此显示。</p></div>';
    }
    function loadingPanel(title) {
        return '<div class="kb-panel"><div class="kb-panel-body"><p style="color:var(--text-muted)">' +
               (title || '加载中…') + '</p></div></div>';
    }

    // --- toggle helpers ---
    // Each slot tracks what it currently shows (data-kb-mode) so the SAME title-bar
    // button that opened it can close it on a second click (no need for the inner ×).
    function slotMode(root) { return root ? (root.getAttribute('data-kb-mode') || '') : ''; }
    function clearActive(root) {
        var card = root && root.closest ? root.closest('.round-card') : null;
        if (card) card.querySelectorAll('.kb-toggle.active').forEach(function (b) { b.classList.remove('active'); });
    }
    function resetSlot(root) {
        swapInto(root, placeholder());
        if (root) root.setAttribute('data-kb-mode', '');
        clearActive(root);
    }
    // Clicking the active button again closes the slot; otherwise open + mark active.
    function openOrToggle(btn, root, mode, load) {
        if (!root) return;
        if (root.id !== 'kb-modal-body' && slotMode(root) === mode) { resetSlot(root); return; }
        clearActive(root);
        if (btn) btn.classList.add('active');
        root.setAttribute('data-kb-mode', mode);
        load(root);
    }

    // Distillation panel into a round's inline slot (detail page) or the modal.
    window.kbOpenRound = function (btn, source, galaxy, ts, round) {
        var root = document.getElementById('kb-round-' + round) ||
                   document.getElementById('kb-modal-body');
        openOrToggle(btn, root, 'distill', function (root) {
            swapInto(root, loadingPanel('轮次 ' + round));
            fetch('/kb/ajax/panel?source=' + enc(source) + '&galaxy_id=' + enc(galaxy) +
                  '&timestamp_dir=' + enc(ts) + '&round_number=' + enc(round || ''))
                .then(function (r) { return r.text(); }).then(function (h) { swapInto(root, h); })
                .catch(function () { swapInto(root, '<div class="kb-flash kb-flash-err">加载失败</div>'); });
        });
    };

    // Read-only component analysis into the same slot.
    window.kbOpenComp = function (btn, source, galaxy, ts, round) {
        var root = (round && document.getElementById('kb-round-' + round)) ||
                   document.getElementById('kb-modal-body');
        openOrToggle(btn, root, 'comp', function (root) {
            swapInto(root, '<div class="kb-panel"><div class="kb-panel-body comp-body"><p style="color:var(--text-muted)">加载中…</p></div></div>');
            var pb = root.querySelector('.comp-body');
            fetch('/component-analysis/' + enc(source) + '/' + enc(galaxy) + '/' + enc(ts))
                .then(function (r) { return r.text(); }).then(function (h) { pb.innerHTML = h; })
                .catch(function () { pb.innerHTML = '<p style="color:var(--red)">加载成分分析失败</p>'; });
        });
    };

    // Read-only fit log into the same slot (replaces the old log popup).
    window.kbOpenLog = function (btn, source, galaxy, ts, round) {
        var root = document.getElementById('kb-round-' + round) ||
                   document.getElementById('kb-modal-body');
        openOrToggle(btn, root, 'log', function (root) {
            swapInto(root, '<div class="kb-panel"><div class="kb-panel-body"><pre class="kb-log-pre">加载中…</pre></div></div>');
            var pre = root.querySelector('.kb-log-pre');
            fetch('/summary/' + enc(source) + '/' + enc(galaxy) + '/' + enc(ts))
                .then(function (r) { return r.text(); }).then(function (t) { pre.textContent = t; })
                .catch(function () { pre.textContent = '加载日志失败'; });
        });
    };

    // Generic form POST (save / distill / commit): swap the re-rendered panel
    // into the form's OWN host root (DOM-relative).
    function postForm(form, url) {
        var root = rootOf(form);
        if (!root) return;
        var btns = form.querySelectorAll('button');
        btns.forEach(function (b) { b.disabled = true; });
        var fd = new FormData(form);                 // capture BEFORE swapping the form away
        // distill (VLM ~10-30s) and commit (DINOv2 forward + KB write) are slow:
        // swap in an immediate loading state so the click clearly "took".
        var slowMsg = '';
        if (url.indexOf('/kb/ajax/distill') >= 0) {
            slowMsg = '蒸馏中…VLM 调用约 10–30 秒，请勿关闭或刷新本页。';
        } else if (url.indexOf('/kb/ajax/commit') >= 0) {
            slowMsg = '入库中…正在提取特征并写入 live 检索库，请稍候。';
        }
        if (slowMsg) {
            swapInto(root, '<div class="kb-panel"><div class="kb-panel-body kb-loading">' +
                '<span class="kb-spinner"></span><span>' + slowMsg + '</span></div></div>');
        }
        fetch(url, { method: 'POST', body: fd })
            .then(function (r) { return r.text(); })
            .then(function (h) { swapInto(root, h); if (root && root.closest('#kb-modal')) dirty = true; })
            .catch(function () {
                btns.forEach(function (b) { b.disabled = false; });
                swapInto(root, '<div class="kb-flash kb-flash-err">请求失败，请重试</div>');
            });
    }

    window.kbPost = function (ev, url) { ev.preventDefault(); postForm(ev.target, url); return false; };
    // POST a form to a specific URL from a button click — used by STATE A's two
    // buttons: 开始蒸馏 -> /kb/ajax/distill (VLM, slow → spinner), 手动填写 -> /kb/ajax/manual (instant).
    window.kbPostForm = function (btn, url) { var form = btn.closest('form'); if (form) postForm(form, url); };
    window.kbCommit = function (btn) {
        var form = btn.closest('form');
        if (!form) return;
        if (!confirm('确认写入 live 检索库？此操作不易撤销。')) return;
        postForm(form, '/kb/ajax/commit');
    };
    window.kbRedistill = function (btn) {
        var form = btn.closest('form');
        if (!form) return;
        var hint = prompt('重新蒸馏（VLM 调用）。专家提示（可选，留空则无）：', '');
        if (hint === null) return;
        var h = form.querySelector('input[name="hint"]');
        if (!h) { h = document.createElement('input'); h.type = 'hidden'; h.name = 'hint'; form.appendChild(h); }
        h.value = hint;
        postForm(form, '/kb/ajax/distill');
    };

    // × inside a panel: clear its inline slot (also deactivates the title button),
    // or close the modal if that's the host.
    window.kbClose = function (el) {
        var root = rootOf(el);
        if (!root) return;
        if (root.closest('#kb-modal')) { kbCloseModal(); return; }
        resetSlot(root);
    };

    // --- review modal (kb_review page) ---
    // Shows the galaxy comparison image on the left, the editor panel on the right.
    window.kbOpenBySid = function (sid, source, galaxy, ts) {
        var modal = document.getElementById('kb-modal');
        var body = document.getElementById('kb-modal-body');
        if (!modal || !body) return;
        dirty = false;
        // side-by-side: image on the left, editor panel on the right (the
        // panel scrolls independently via .kb-split-form when the form is long).
        var imgHtml = (source && galaxy && ts)
            ? '<aside class="kb-split-img"><img src="/image/' + enc(source) + '/' + enc(galaxy) + '/' + enc(ts) + '" loading="lazy" onclick="kbOpenLightbox(this.src)"></aside>'
            : '';
        body.innerHTML =
            '<div class="kb-split">' + imgHtml +
            '<div class="kb-split-form"><div class="kb-modal-panel" data-kb-root></div></div>' +
            '</div>';
        var panelRoot = body.querySelector('.kb-modal-panel');
        modal.classList.add('active');
        swapInto(panelRoot, loadingPanel(''));
        fetch('/kb/ajax/panel?sid=' + enc(sid))
            .then(function (r) { return r.text(); }).then(function (h) { swapInto(panelRoot, h); })
            .catch(function () { swapInto(panelRoot, '<div class="kb-flash kb-flash-err">加载失败</div>'); });
    };
    window.kbCloseModal = function (e) {
        var m = document.getElementById('kb-modal');
        if (!m) return;
        if (e && e.target !== m && !e.target.classList.contains('modal-close') &&
            !e.target.classList.contains('kb-panel-close')) return;
        m.classList.remove('active');
        if (dirty) { dirty = false; window.location.reload(); }
    };
})();

// --- batch pre-ingest (admin) ---
// The /admin/kb/preingest POST is synchronous and slow (one VLM call per sample,
// ~10-30s each; a whole source takes minutes). Submit it via fetch (no navigation
// so the page stays alive) and poll /progress every few seconds to show drafts
// accumulating. Polling is BOUNDED: stop() runs the instant the POST resolves or
// rejects, and dies with the tab if closed — it never polls forever.
window.kbBatchPreingest = function (src) {
    if (!src) return;
    if (!confirm('对 ' + src + ' 批量预蒸馏？VLM 每样本约 10–30 秒。期间可关闭本页，后端会继续处理；稍后在 /kb/review 查看结果（勿重启 KB 服务）。')) return;
    var ov = document.getElementById('kb-batch-overlay');
    var progEl = document.getElementById('kb-batch-progress');
    if (ov) ov.classList.add('active');
    if (progEl) progEl.textContent = '准备中…';
    var timer = null;
    function stop() { if (timer) { clearInterval(timer); timer = null; } }

    // poll the live draft count only while the batch POST is in flight
    timer = setInterval(function () {
        fetch('/admin/kb/preingest/progress?src=' + encodeURIComponent(src), { cache: 'no-store' })
            .then(function (r) { return r.json(); })
            .then(function (p) {
                if (progEl && p) progEl.textContent =
                    '已生成草稿 ' + (p.staged || 0) + ' 条（源共 ' + (p.total || '?') + ' 个样本）';
            }).catch(function () { /* a missed poll is harmless */ });
    }, 4000);

    fetch('/admin/kb/preingest/' + encodeURIComponent(src), { method: 'POST' })
        .then(function (r) {
            stop();
            if (r.ok) {
                // POST 302-> /kb/review?batch_ok=... : follow it (lands on the summary banner).
                window.location.href = r.url;
            } else {
                if (ov) ov.classList.remove('active');
                alert('批量预蒸馏出错（HTTP ' + r.status + '）。已完成的草稿已保存，请在 /kb/review 查看。');
            }
        })
        .catch(function () {
            stop();
            if (ov) ov.classList.remove('active');
            alert('批量预蒸馏请求失败（网络中断？）。已完成的草稿已保存，请在 /kb/review 查看。');
        });
};

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

    // Expose so modal images can open the lightbox too: the auto-binder below
    // only covers detail-page images, and modal images are injected after
    // DOMContentLoaded (so the binder never sees them). Resets zoom/pan per open.
    window.kbOpenLightbox = openLightbox;

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
