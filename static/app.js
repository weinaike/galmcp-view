/* === Galaxy Fitting Label Tool - Client-side JS === */

// Toggle log section visibility, lazy-load content on first open
function toggleLog(btn, galaxyId, timestampDir) {
    var roundCard = btn.closest('.round-card');
    var logSection = roundCard.querySelector('.log-section:not(.comp-analysis-section)');
    if (!logSection) return;

    var isHidden = logSection.style.display === 'none';

    if (isHidden) {
        // First time opening: fetch content if not already loaded
        var preEl = logSection.querySelector('.log-content');
        if (preEl && !preEl.textContent) {
            fetch('/summary/' + galaxyId + '/' + timestampDir)
                .then(function(resp) { return resp.text(); })
                .then(function(text) { preEl.textContent = text; })
                .catch(function() { preEl.textContent = '加载日志失败'; });
        }
        logSection.style.display = 'block';
        btn.innerHTML = '&#128196; 收起日志';
    } else {
        logSection.style.display = 'none';
        btn.innerHTML = '&#128196; 查看日志';
    }
}

// Toggle component analysis section visibility, lazy-load content on first open
function toggleCompAnalysis(btn, galaxyId, timestampDir) {
    var roundCard = btn.closest('.round-card');
    var section = roundCard.querySelector('.comp-analysis-section');
    if (!section) return;

    var isHidden = section.style.display === 'none';

    if (isHidden) {
        var contentEl = section.querySelector('.comp-analysis-content');
        if (contentEl && !contentEl.innerHTML) {
            fetch('/component-analysis/' + galaxyId + '/' + timestampDir)
                .then(function(resp) { return resp.text(); })
                .then(function(html) { contentEl.innerHTML = html; })
                .catch(function() { contentEl.innerHTML = '<p style="color:var(--red)">加载成分分析失败</p>'; });
        }
        section.style.display = 'block';
        btn.innerHTML = '&#128269; 收起成分分析';
    } else {
        section.style.display = 'none';
        btn.innerHTML = '&#128269; 显示成分分析';
    }
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

function openReportModal(galaxyId) {
    var modal = document.getElementById('report-modal');
    var body = document.getElementById('report-body');
    if (!modal || !body) return;
    body.innerHTML = '<p style="color:var(--text-muted)">加载中…</p>';
    modal.classList.add('active');

    fetch('/analysis-report/' + galaxyId)
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
