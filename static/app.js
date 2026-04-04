/* === Galaxy Fitting Label Tool - Client-side JS === */

// Toggle parameter help box
function toggleParamHelp(btn) {
    var helpBox = btn.closest('.components-section').querySelector('.param-help-box');
    if (helpBox) {
        var isHidden = helpBox.style.display === 'none';
        helpBox.style.display = isHidden ? 'block' : 'none';
        btn.textContent = isHidden ? '\u2715 关闭说明' : '\u2139 参数说明';
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
