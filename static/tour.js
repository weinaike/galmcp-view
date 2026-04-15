/* === Interactive Tour Guide === */

(function() {
    'use strict';

    var overlay = null;
    var tooltip = null;
    var currentStep = 0;
    var steps = [];
    var origOverflow = '';

    // --- Tour steps per page ---
    function getSteps() {
        var path = window.location.pathname;

        // Sample list page (/voting/)
        if (path === '/voting/' || path === '/voting') {
            return [
                { selector: '.nav-brand a', title: '首页导航',
                  body: '点击「星系拟合评价系统」可随时回到样本列表首页。' },
                { selector: '.nav-links a[href="/voting/"]', title: '拟合投票',
                  body: '这是当前页面——样本列表。展示所有待评价的星系拟合结果。' },
                { selector: '.nav-links a[href="/statistics"]', title: '投票统计',
                  body: '点击查看所有人的投票汇总统计，包括一致性和完成情况。' },
                { selector: '.progress-text', title: '评价进度',
                  body: '显示你已评价的样本数和总数。' },
                { selector: '.filter-bar', title: '筛选按钮',
                  body: '快速切换查看全部样本、已评价或未评价的样本。' },
                { selector: '.sample-table th:last-child', title: '操作列',
                  body: '点击「查看」进入对应星系的详细拟合结果和投票页面。' },
            ];
        }

        // Sample detail page (/sample/xxx)
        if (/^\/sample\//.test(path)) {
            var s = [
                { selector: '.back-link', title: '返回列表',
                  body: '点击返回星系样本列表页面。' },
            ];
            if (document.querySelector('.btn-report')) {
                s.push({ selector: '.btn-report', title: 'Agent 分析报告',
                    body: '点击打开 AI 自动生成的分析报告弹窗，查看对拟合结果的分析总结。' });
            }
            if (document.querySelector('.s4g-section')) {
                s.push({ selector: '.s4g-section', title: 'S4G 真实成分',
                    body: '这里展示该星系在 S4G 表中的真实成分参数，供你参考对比。' });
            }
            s.push({ selector: '.rounds-section .round-card:first-child', title: '拟合轮次卡片',
                body: '每个卡片代表一轮拟合结果。黄色边框+星标的是 AI 推荐的最佳轮次。' });
            if (document.querySelector('.log-toggle')) {
                s.push({ selector: '.log-toggle', title: '查看日志 / 成分分析',
                    body: '点击可展开/收起该轮次的拟合日志和成分分析详情。' });
            }
            s.push(
                { selector: '#perfect-yes', title: '投票：是否可接受',
                    body: '选择「是」表示已有可接受的拟合轮次；选择「否」表示需要继续拟合。' },
                { selector: '#best-round', title: '选择最佳轮次',
                    body: '当你选择「是」时，必须在这里选出你认为最佳的拟合轮次。选择后对应的轮次卡片会高亮。' },
                { selector: '#reason', title: '选择理由',
                    body: '说明你选择该轮次作为最佳的理由。建议使用语音输入法提高效率。' },
                { selector: '.vote-section .btn-primary', title: '保存投票',
                    body: '填写完评价后点击保存。投票可重复修改。' }
            );
            if (document.querySelector('.header-actions .btn-primary')) {
                s.push({ selector: '.header-actions .btn-primary', title: '下一个未评价',
                    body: '点击直接跳到下一个尚未评价的星系，快速推进进度。' });
            }
            return s;
        }

        // Analysis list page (/analysis/)
        if (path === '/analysis/' || path === '/analysis') {
            return [
                { selector: '.progress-text', title: '评价进度',
                  body: '显示你已完成评价的星系数和总数。' },
                { selector: '.filter-bar', title: '筛选按钮',
                  body: '快速切换全部、已评价、未评价的星系。' },
                { selector: '.sample-table td:last-child a', title: '评价按钮',
                  body: '点击进入对应星系的分析评价页面，查看 AI 分析并打分。' },
            ];
        }

        // Analysis eval page (/analysis/eval/xxx)
        if (/^\/analysis\/eval\//.test(path)) {
            return [
                { selector: '.back-link', title: '返回列表',
                  body: '点击返回分析评价列表。' },
                { selector: '.eval-image-section', title: '残差对比图',
                  body: '左侧展示星系的残差对比图，是评分的主要依据。' },
                { selector: '.eval-right', title: 'AI 分析报告',
                  body: '右侧展示 AI 生成的分析报告，包含原图描述、残差描述和成分预测。' },
                { selector: '.rating-note', title: '评分说明',
                  body: '请重点关注第一部分（原图描述）和第二部分（成分预测），第三部分仅供参考。' },
                { selector: '.star-group', title: '星级评分',
                  body: '分别为「原图理解」「残差理解」「成分预测」打 1-5 星。鼠标悬停可预览评分等级。' },
                { selector: '.dim-feedback', title: '文字反馈',
                  body: '可对每个维度补充文字反馈。建议使用语音输入法提高效率。' },
                { selector: '.btn-full', title: '保存评价',
                  body: '完成所有评分后点击保存。评价可重复修改。' },
            ];
        }

        // Statistics page
        if (path === '/statistics') {
            return [
                { selector: '.nav-links a[href="/voting/"]', title: '返回投票',
                  body: '点击返回拟合投票页面继续评价。' },
            ];
        }

        // Analysis statistics
        if (path === '/analysis/statistics') {
            return [
                { selector: '.back-link, .eval-topbar .back-link', title: '返回列表',
                  body: '点击返回分析评价列表。' },
            ];
        }

        return [];
    }

    // --- Core functions ---

    function createOverlay() {
        overlay = document.createElement('div');
        overlay.className = 'tour-overlay hidden';
        overlay.addEventListener('click', function() { endTour(); });
        document.body.appendChild(overlay);
    }

    function createTooltip() {
        tooltip = document.createElement('div');
        tooltip.className = 'tour-tooltip';
        tooltip.style.display = 'none';
        document.body.appendChild(tooltip);
    }

    function positionTooltip(target) {
        if (!tooltip || !target) return;
        var rect = target.getBoundingClientRect();
        var tw = 360;

        // Try placing below the target
        var top = rect.bottom + 12;
        var left = rect.left + rect.width / 2 - tw / 2;

        // Clamp to viewport
        if (left < 12) left = 12;
        if (left + tw > window.innerWidth - 12) left = window.innerWidth - tw - 12;

        // If overflows bottom, place above
        if (top + 200 > window.innerHeight) {
            top = rect.top - 12 - 160;
            if (top < 12) top = 12;
        }

        tooltip.style.top = top + 'px';
        tooltip.style.left = left + 'px';
        tooltip.style.display = 'block';
    }

    function scrollToTarget(target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    function showStep() {
        if (currentStep >= steps.length) {
            endTour();
            return;
        }

        // Remove previous highlight
        var prev = document.querySelector('.tour-highlight');
        if (prev) prev.classList.remove('tour-highlight');

        var step = steps[currentStep];
        var target = document.querySelector(step.selector);
        if (!target) {
            // Skip missing elements
            currentStep++;
            showStep();
            return;
        }

        scrollToTarget(target);

        // Highlight
        target.classList.add('tour-highlight');

        // Show overlay
        overlay.className = 'tour-overlay';

        // Build tooltip
        tooltip.innerHTML =
            '<div class="tour-tooltip-title">' + step.title + '</div>' +
            '<div class="tour-tooltip-body">' + step.body + '</div>' +
            '<div class="tour-tooltip-footer">' +
                '<span class="tour-step-indicator">' + (currentStep + 1) + ' / ' + steps.length + '</span>' +
                '<div class="tour-btns">' +
                    (currentStep > 0
                        ? '<button class="tour-btn" onclick="tourPrev()">上一步</button>'
                        : '<button class="tour-btn" onclick="endTour()">跳过</button>') +
                    (currentStep < steps.length - 1
                        ? '<button class="tour-btn tour-btn-primary" onclick="tourNext()">下一步</button>'
                        : '<button class="tour-btn tour-btn-primary" onclick="endTour()">完成</button>') +
                '</div>' +
            '</div>';

        positionTooltip(target);
    }

    function startTour() {
        steps = getSteps();
        if (steps.length === 0) return;
        currentStep = 0;
        origOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        if (!overlay) createOverlay();
        if (!tooltip) createTooltip();
        showStep();
    }

    function endTour() {
        var prev = document.querySelector('.tour-highlight');
        if (prev) prev.classList.remove('tour-highlight');
        if (overlay) overlay.className = 'tour-overlay hidden';
        if (tooltip) tooltip.style.display = 'none';
        document.body.style.overflow = origOverflow;
        currentStep = 0;
    }

    function tourNext() {
        currentStep++;
        showStep();
    }

    function tourPrev() {
        if (currentStep > 0) currentStep--;
        showStep();
    }

    // Expose globally
    window.startTour = startTour;
    window.tourNext = tourNext;
    window.tourPrev = tourPrev;
    window.endTour = endTour;

    // Auto-start for first-time users
    document.addEventListener('DOMContentLoaded', function() {
        if (!sessionStorage.getItem('tour_done') && document.querySelector('.tour-start-btn')) {
            var steps = getSteps();
            if (steps.length > 0) {
                setTimeout(function() { startTour(); sessionStorage.setItem('tour_done', '1'); }, 800);
            }
        }
    });

})();
