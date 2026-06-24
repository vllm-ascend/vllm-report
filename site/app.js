(function () {
  const REPOS = ['vllm-project/vllm', 'vllm-project/vllm-ascend'];
  const DATA_BASE_LOCAL = '../data';
  const DATA_BASE_PAGES = './data';

  let DATA_BASE = DATA_BASE_PAGES;

  async function detectDataBase() {
    const testUrl = `${DATA_BASE_PAGES}/vllm/dates.json`;
    try {
      const resp = await fetch(testUrl, { method: 'HEAD' });
      if (resp.ok) {
        DATA_BASE = DATA_BASE_PAGES;
        return;
      }
    } catch {}
    DATA_BASE = DATA_BASE_LOCAL;
  }

  let currentRepo = REPOS[0];
  let availableDates = [];
  let currentDateIndex = -1;
  let commitsData = null;
  let analysisData = null;
  let activeFilter = 'all';
  let searchQuery = '';
  let analysisDates = [];
  let crossDayResults = null; // { commits: [...], analysis: {...} } from cross-day search

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  function repoDir(repo) {
    if (repo === 'vllm-project/vllm') return 'vllm';
    if (repo === 'vllm-project/vllm-ascend') return 'vllm-ascend';
    return repo.split('/').pop();
  }

  function dataUrl(repo, type, date) {
    return `${DATA_BASE}/${repoDir(repo)}/${type}/${date}.json`;
  }

  async function fetchJSON(url) {
    try {
      const resp = await fetch(url);
      if (!resp.ok) return null;
      return await resp.json();
    } catch {
      return null;
    }
  }

  async function loadAvailableDates() {
    const index = await fetchJSON(`${DATA_BASE}/${repoDir(currentRepo)}/dates.json`);
    if (index && index.dates && index.dates.length > 0) {
      availableDates = index.dates.sort().reverse();
      return;
    }

    availableDates = [];
    const meta = await fetchJSON(`${DATA_BASE}/${repoDir(currentRepo)}/meta.json`);
    if (meta && meta.last_fetch_time) {
      const endStr = cnDateStr(new Date(meta.last_fetch_time));
      const todayStr = cnDateStr(new Date());
      const start = new Date(todayStr + 'T00:00:00+08:00');
      const end = new Date(endStr + 'T00:00:00+08:00');
      start.setFullYear(start.getFullYear() - 1);

      const candidates = [];
      const d = new Date(start);
      while (d <= end) {
        candidates.push(cnDateStr(d));
        d.setDate(d.getDate() + 1);
      }
      candidates.sort().reverse();

      for (const date of candidates) {
        const resp = await fetch(dataUrl(currentRepo, 'commits', date), { method: 'HEAD' });
        if (resp.ok) {
          availableDates.push(date);
        }
      }
    }

    if (availableDates.length === 0) {
      availableDates.push(cnDateStr(new Date()));
    }
  }

  async function loadAnalysisDates() {
    var data = await fetchJSON(DATA_BASE + '/' + repoDir(currentRepo) + '/analysis-dates.json');
    if (data && data.dates) {
      analysisDates = data.dates;
    } else {
      analysisDates = [];
    }
  }

  function cnDateStr(d) {
    const cnOffset = 8 * 60;
    const utc = d.getTime() + d.getTimezoneOffset() * 60000;
    const cn = new Date(utc + cnOffset * 60000);
    const y = cn.getFullYear();
    const m = String(cn.getMonth() + 1).padStart(2, '0');
    const day = String(cn.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  function parseCnTime(isoStr) {
    if (!isoStr) return null;
    if (isoStr.includes('+08:00') || isoStr.includes('T')) {
      const d = new Date(isoStr);
      if (!isNaN(d.getTime())) return d;
    }
    return null;
  }

  function formatTime(isoStr) {
    const d = parseCnTime(isoStr);
    if (!d) {
      if (isoStr && isoStr.length >= 16) return isoStr.slice(11, 16);
      return '--';
    }
    const cn = new Date(d.getTime() + (8 * 60 + d.getTimezoneOffset()) * 60000);
    return String(cn.getHours()).padStart(2, '0') + ':' + String(cn.getMinutes()).padStart(2, '0');
  }

  async function loadDate(date) {
    showLoading(true);
    commitsData = null;
    analysisData = null;
    crossDayResults = null;
    searchQuery = '';
    $('#searchInput').value = '';

    const [commits, analysis] = await Promise.all([
      fetchJSON(dataUrl(currentRepo, 'commits', date)),
      fetchJSON(dataUrl(currentRepo, 'analysis', date)),
    ]);

    commitsData = commits;
    analysisData = analysis;
    showLoading(false);
    render();
  }

  async function loadCommitsForRange(startStr, endStr) {
    const datesInRange = [];
    const d = new Date(startStr + 'T00:00:00+08:00');
    const end = new Date(endStr + 'T00:00:00+08:00');
    while (d <= end) {
      const ds = cnDateStr(d);
      if (availableDates.includes(ds)) {
        datesInRange.push(ds);
      }
      d.setDate(d.getDate() + 1);
    }

    const [commitResults, analysisResults] = await Promise.all([
      Promise.all(datesInRange.map(function (date) {
        return fetchJSON(dataUrl(currentRepo, 'commits', date));
      })),
      Promise.all(datesInRange.map(function (date) {
        return fetchJSON(dataUrl(currentRepo, 'analysis', date));
      })),
    ]);

    var allCommits = [];
    var allAnalysis = {};

    for (var i = 0; i < commitResults.length; i++) {
      if (commitResults[i] && commitResults[i].commits) {
        commitResults[i].commits.forEach(function (c) {
          c._exportDate = datesInRange[i];
          allCommits.push(c);
        });
      }
    }

    for (var j = 0; j < analysisResults.length; j++) {
      if (analysisResults[j] && analysisResults[j].commits) {
        analysisResults[j].commits.forEach(function (a) {
          allAnalysis[a.sha] = a;
        });
      }
    }

    return { commits: allCommits, analysis: allAnalysis };
  }

  async function exportToExcel() {
    var startDate = $('#rangeStart').value;
    var endDate = $('#rangeEnd').value;

    if (!startDate || !endDate) {
      alert('Please select both start and end dates');
      return;
    }

    if (startDate > endDate) {
      alert('Start date must be before end date');
      return;
    }

    var btn = $('#exportBtn');
    var originalText = btn.textContent;
    btn.textContent = 'Exporting...';
    btn.disabled = true;

    try {
      var result = await loadCommitsForRange(startDate, endDate);

      if (result.commits.length === 0) {
        alert('No commits found in the selected date range');
        btn.textContent = originalText;
        btn.disabled = false;
        return;
      }

      var headers = ['SHA', 'Date', 'Author', 'Title', 'Tags', 'Ascend Affected', 'Needs Test Update', 'AI Analysis', 'Test Impact Reason', 'Changed Files', 'Additions', 'Deletions', 'Files Changed'];
      var rows = [headers];

      result.commits.forEach(function (commit) {
        var a = result.analysis[commit.sha] || null;
        var tags = a && a.tags ? a.tags.join(', ') : '';
        var ascendAffected = a && a.ascend_impact && a.ascend_impact.ascend_affected ? 'Yes' : '';
        var needsTest = a && (
          (a.test_impact && a.test_impact.needs_test_update) ||
          (a.ascend_impact && a.ascend_impact.needs_test_update)
        ) ? 'Yes' : '';
        var comment = a && a.comment ? a.comment : '';
        var testReason = '';
        if (a && a.test_impact && a.test_impact.reason) {
          testReason = a.test_impact.reason;
        } else if (a && a.ascend_impact && a.ascend_impact.needs_test_update) {
          testReason = a.ascend_impact.testing || '';
        }
        var fileList = (commit.files || []).map(function (f) { return f.filename; }).join('; ');
        var title = commit.message.split('\n')[0];
        var additions = commit.stats ? commit.stats.total_additions : 0;
        var deletions = commit.stats ? commit.stats.total_deletions : 0;
        var files = commit.stats ? commit.stats.files_changed : 0;
        var author = (commit.author && commit.author.name) || 'unknown';

        rows.push([
          commit.sha,
          commit._exportDate,
          author,
          title,
          tags,
          ascendAffected,
          needsTest,
          comment,
          testReason,
          fileList,
          additions,
          deletions,
          files
        ]);
      });

      var wb = XLSX.utils.book_new();
      var ws = XLSX.utils.aoa_to_sheet(rows);

      var colWidths = headers.map(function (_, i) {
        var maxLen = headers[i].length;
        rows.forEach(function (row) {
          var len = String(row[i] || '').length;
          if (len > maxLen) maxLen = len;
        });
        return { wch: Math.min(maxLen + 3, 80) };
      });
      ws['!cols'] = colWidths;

      XLSX.utils.book_append_sheet(wb, ws, 'Commits');

      var wbout = XLSX.write(wb, { bookType: 'xlsx', type: 'array' });
      var blob = new Blob([wbout], { type: 'application/octet-stream' });
      var link = document.createElement('a');
      var prefix = repoDir(currentRepo);
      link.href = URL.createObjectURL(blob);
      link.download = prefix + '-commits-' + startDate + '-' + endDate + '.xlsx';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(link.href);
    } catch (err) {
      alert('Export failed: ' + (err.message || 'unknown error'));
    } finally {
      btn.textContent = originalText;
      btn.disabled = false;
    }
  }

  function showLoading(show) {
    $('#loading').style.display = show ? 'flex' : 'none';
    if (show) {
      $('#commitList').innerHTML = '';
      $('#emptyState').style.display = 'none';
    }
  }

  function getAnalysisForSha(sha) {
    if (!analysisData || !analysisData.commits) return null;
    return analysisData.commits.find((c) => c.sha === sha);
  }

  function filterCommits(commits, analysisMap) {
    if (!commits) return [];
    return commits.filter((c) => {
      var a = analysisMap ? analysisMap[c.sha] : getAnalysisForSha(c.sha);
      const q = searchQuery.toLowerCase();
      const matchesSearch =
        !q ||
        c.message.toLowerCase().includes(q) ||
        c.sha.toLowerCase().includes(q) ||
        (c.author && c.author.name.toLowerCase().includes(q)) ||
        (a && a.comment && a.comment.toLowerCase().includes(q)) ||
        (c.files && c.files.some(f => f.filename.toLowerCase().includes(q)));

      if (!matchesSearch) return false;

      if (activeFilter === 'all') return true;
      if (!a) return false;

      switch (activeFilter) {
        case 'needs-test':
          return (a.test_impact && a.test_impact.needs_test_update) || (a.ascend_impact && a.ascend_impact.needs_test_update);
        case 'affects-ascend':
          return a.ascend_impact && a.ascend_impact.ascend_affected === true;
        case 'high-risk':
          return a.tags && a.tags.some((t) => t === 'high-risk');
        default:
          return a.tags && a.tags.includes(activeFilter);
      }
    });
  }

  function renderStats(commits) {
    let totalAdd = 0, totalDel = 0, totalFiles = 0;
    (commits || []).forEach((c) => {
      if (c.stats) {
        totalAdd += c.stats.total_additions || 0;
        totalDel += c.stats.total_deletions || 0;
        totalFiles += c.stats.files_changed || 0;
      }
    });
    $('#statCommits').textContent = commits ? commits.length : 0;
    $('#statAdditions').textContent = '+' + totalAdd.toLocaleString();
    $('#statDeletions').textContent = '-' + totalDel.toLocaleString();
    $('#statFiles').textContent = totalFiles;
  }

  function updateFilterChips(allCommits) {
    var filters = ['all', 'needs-test', 'affects-ascend', 'high-risk', 'feature', 'bugfix', 'refactor', 'performance'];
    var saved = activeFilter;
    var counts = {};
    for (var i = 0; i < filters.length; i++) {
      activeFilter = filters[i];
      counts[filters[i]] = filterCommits(allCommits).length;
    }
    activeFilter = saved;
    $$('.filter-chip').forEach(function (chip) {
      var f = chip.dataset.filter;
      var count = counts[f] || 0;
      var label = chip.textContent.replace(/\s*\(\d+\)$/, '');
      chip.textContent = label + ' (' + count + ')';
    });
  }

  function renderSummary() {
    const el = $('#dailySummary');
    if (analysisData && analysisData.daily_summary) {
      el.style.display = 'block';
      $('#summaryText').innerHTML = renderMarkdown(analysisData.daily_summary);
    } else {
      el.style.display = 'none';
    }
  }

  function tagClass(tag) {
    if (tag === 'high-risk') return 'tag risk-high';
    if (tag === 'medium-risk') return 'tag risk-medium';
    if (tag === 'low-risk') return 'tag risk-low';
    if (['feature', 'bugfix', 'refactor', 'performance'].includes(tag)) return `tag type-${tag}`;
    return 'tag';
  }

  function renderDiff(patch) {
    if (!patch) return '';
    const lines = patch.split('\n');
    let html = '';
    for (const line of lines) {
      let cls = 'line-ctx';
      let content = escapeHtml(line);
      if (line.startsWith('@@')) cls = 'line-hunk';
      else if (line.startsWith('+')) cls = 'line-add';
      else if (line.startsWith('-')) cls = 'line-del';
      html += `<div class="${cls}">${content}</div>`;
    }
    return html;
  }

  function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function highlightText(text, query) {
    if (!query) return escapeHtml(text);
    var escaped = escapeHtml(text);
    var q = escapeHtml(query).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var re = new RegExp('(' + q + ')', 'gi');
    return escaped.replace(re, '<mark>$1</mark>');
  }

  function renderMarkdown(str) {
    if (!str) return '';
    // First escape HTML to prevent XSS
    var s = escapeHtml(str);
    // Code blocks (```...```) - must be done before inline code
    s = s.replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
      return '<pre><code>' + code.trim() + '</code></pre>';
    });
    // Inline code `...`
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold **text** or __text__
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__(.+?)__/g, '<strong>$1</strong>');
    // Italic *text* or _text_ (single, but not inside words)
    s = s.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
    s = s.replace(/(?<![:\w])_([^_\n]+)_(?![:\w])/g, '<em>$1</em>');
    // Links [text](url) — sanitize to http/https only
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, text, url) {
      url = url.trim();
      if (!url.startsWith('http://') && !url.startsWith('https://')) return text;
      return '<a href="' + escapeHtml(url) + '" target="_blank">' + text + '</a>';
    });
    // ### headings
    s = s.replace(/^### (.+)$/gm, '<h4>$1</h4>');
    s = s.replace(/^## (.+)$/gm, '<h3>$1</h3>');
    s = s.replace(/^# (.+)$/gm, '<h2>$1</h2>');
    // Unordered list items - wrap consecutive - items in <ul>
    s = s.replace(/^- (.+)$/gm, '<li>$1</li>');
    s = s.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
    // Ordered list items
    s = s.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    s = s.replace(/(?:<li>.*<\/li>\n?)+/g, function (match) {
      if (match.indexOf('<ul>') === -1) {
        return '<ol>' + match + '</ol>';
      }
      return match;
    });
    // Double line breaks = new paragraph
    s = s.replace(/\n\n/g, '</p><p>');
    // Single line break
    s = s.replace(/\n/g, '<br>');
    // Wrap in paragraph if not already wrapped
    if (!s.startsWith('<')) {
      s = '<p>' + s + '</p>';
    }
    return s;
  }

  function renderCommitCard(commit) {
    const a = getAnalysisForSha(commit.sha);
    const hasAnalysis = !!a;
    const isHighRisk = a && a.tags && a.tags.includes('high-risk');

    const title = commit.message.split('\n')[0];
    const body = commit.message.split('\n').slice(1).join('\n').trim();
    const shaShort = commit.sha.slice(0, 8);
    const additions = commit.stats ? commit.stats.total_additions : 0;
    const deletions = commit.stats ? commit.stats.total_deletions : 0;
    const files = commit.stats ? commit.stats.files_changed : 0;

    let cardClass = 'commit-card';
    if (hasAnalysis) cardClass += ' has-analysis';
    if (isHighRisk) cardClass += ' high-risk';

    let html = `<div class="${cardClass}" data-sha="${commit.sha}">`;
    html += `<div class="commit-header">`;
    html += `<span class="expand-arrow">\u25B6</span>`;
    html += `<a class="commit-sha" href="https://github.com/${currentRepo}/commit/${commit.sha}" target="_blank">${shaShort}</a>`;
    html += `<div class="commit-message">`;
    html += `<div class="commit-title">${highlightText(title, searchQuery)}</div>`;
    if (body) html += `<div class="commit-body">${escapeHtml(body)}</div>`;
    html += `</div>`;

    if (hasAnalysis && a.tags) {
      html += `<div class="tag-list">`;
      for (const tag of a.tags) {
        html += `<span class="${tagClass(tag)}">${escapeHtml(tag)}</span>`;
      }
      html += `</div>`;
    }

    html += `<div class="commit-meta">`;
    html += `<span class="commit-author">${escapeHtml((commit.author && commit.author.name) || 'unknown')}</span>`;
    html += `<span class="commit-time">${formatTime(commit.date)}</span>`;
    html += `<span class="stat-badge additions">+${additions}</span>`;
    html += `<span class="stat-badge deletions">-${deletions}</span>`;
    html += `<span class="stat-badge files">${files}f</span>`;
    html += `</div>`;
    html += `</div>`;

    if (hasAnalysis) {
      html += `<div class="analysis-section">`;
      if (a.comment || a.content) {
        html += `<div class="ai-comment"><div class="ai-label">AI Analysis</div>${renderMarkdown(a.comment || a.content)}</div>`;
      }
      if (a.test_impact) {
        html += `<div class="impact-card test-impact">`;
        html += `<div class="impact-label${a.test_impact.needs_test_update ? ' needs-test' : ''}">${a.test_impact.needs_test_update ? '\u26A0 Test Update Needed' : 'Test Impact'}</div>`;
        html += `<div class="impact-text"><strong>Reason:</strong> ${renderMarkdown(a.test_impact.reason || '')}</div>`;
        if (a.test_impact.suggested_test_areas && a.test_impact.suggested_test_areas.length > 0) {
          html += `<div class="impact-text" style="margin-top:4px"><strong>Areas:</strong> ${a.test_impact.suggested_test_areas.map(escapeHtml).join(', ')}</div>`;
        }
        html += `</div>`;
      }
      if (a.ascend_impact) {
        const funcImp = a.ascend_impact.functionality || '';
        const testImp = a.ascend_impact.testing || '';
        html += `<div class="impact-card ascend-impact">`;
        html += `<div class="impact-label ascend">Ascend Impact</div>`;
        if (funcImp) {
          html += `<div class="impact-text"><strong>Functionality:</strong> ${renderMarkdown(funcImp)}</div>`;
        }
        if (testImp) {
          html += `<div class="impact-text" style="margin-top:4px"><strong>Testing:</strong> ${renderMarkdown(testImp)}</div>`;
        }
        if (a.ascend_impact.needs_test_update) {
          html += `<div class="impact-text" style="margin-top:4px"><span class="stat-badge additions" style="font-size:0.75rem">\u26A0 Test Update Needed</span></div>`;
          if (a.ascend_impact.suggested_test_areas && a.ascend_impact.suggested_test_areas.length > 0) {
            html += `<div class="impact-text" style="margin-top:4px"><strong>Areas:</strong> ${a.ascend_impact.suggested_test_areas.map(escapeHtml).join(', ')}</div>`;
          }
        }
        html += `</div>`;
      }
      html += `</div>`;
    }

    if (commit.files && commit.files.length > 0) {
      html += `<div class="diff-section">`;
      html += `<div class="diff-toggle" data-sha="${commit.sha}"><span class="arrow">\u25B6</span> ${commit.files.length} file(s) changed</div>`;
      html += `<div class="diff-content" id="diff-${commit.sha}">`;
      for (const file of commit.files) {
        html += `<div class="file-diff">`;
        html += `<div class="file-diff-header">`;
        html += `<span class="filename">${escapeHtml(file.filename)}</span>`;
        html += `<div class="file-stats"><span class="add">+${file.additions}</span><span class="del">-${file.deletions}</span></div>`;
        html += `</div>`;
        html += `<div class="file-diff-body"><pre>${renderDiff(file.patch)}</pre></div>`;
        html += `</div>`;
      }
      html += `</div></div>`;
    }

    html += `</div>`;
    return html;
  }

  var currentMonth = null;

  function renderDateBar() {
    var targetDate;
    if (currentMonth) {
      targetDate = cnDateStr(currentMonth);
    } else if (availableDates.length > 0 && currentDateIndex < availableDates.length) {
      targetDate = availableDates[currentDateIndex];
    } else {
      targetDate = cnDateStr(new Date());
    }
    var ref = new Date(targetDate + 'T00:00:00+08:00');
    var year = ref.getFullYear();
    var mon = ref.getMonth(); // 0-indexed

    var firstDay = new Date(year, mon, 1);
    var lastDay = new Date(year, mon + 1, 0);
    var startDow = firstDay.getDay(); // 0=Sun

    var monthNames = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    var weekdayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    var currentDate = availableDates[currentDateIndex];
    var todayStr = cnDateStr(new Date());
    var html = '';

    // Header: prev + month label + next
    var prevMonth = mon - 1;
    var prevYear = year;
    if (prevMonth < 0) { prevMonth = 11; prevYear--; }
    var nextMonth = mon + 1;
    var nextYear = year;
    if (nextMonth > 11) { nextMonth = 0; nextYear++; }
    var nextDisabled = nextYear > new Date().getFullYear() || (nextYear === new Date().getFullYear() && nextMonth > new Date().getMonth());

    html += '<div class="calendar-header">';
    html += '<button class="date-nav" id="datePrev" data-offset="' + prevYear + '-' + String(prevMonth + 1).padStart(2, '0') + '">◀</button>';
    html += '<span class="month-label">' + monthNames[mon] + ' ' + year + '</span>';
    html += '<button class="date-nav" id="dateNext" data-offset="' + nextYear + '-' + String(nextMonth + 1).padStart(2, '0') + '"' + (nextDisabled ? ' disabled' : '') + '>▶</button>';
    html += '</div>';

    // Weekday headers
    html += '<div class="calendar-grid">';
    for (var w = 0; w < 7; w++) {
      html += '<div class="calendar-weekday">' + weekdayNames[w] + '</div>';
    }

    // Fill leading empty cells
    for (var i = 0; i < startDow; i++) {
      html += '<div class="calendar-cell empty"></div>';
    }

    // Days of the month
    var d = new Date(firstDay);
    while (d <= lastDay) {
      var ds = cnDateStr(d);
      var dayNum = d.getDate();
      var hasData = availableDates.indexOf(ds) !== -1;
      var hasAnalysis = analysisDates.indexOf(ds) !== -1;
      var isActive = ds === currentDate;
      var isToday = ds === todayStr;
      var cls = 'calendar-cell';
      if (hasData) cls += ' has-data';
      if (hasAnalysis && hasData) cls += ' has-analysis';
      if (isActive) cls += ' active';
      if (isToday) cls += ' today';
      html += '<div class="' + cls + '" data-date="' + ds + '">' + dayNum + '</div>';
      d.setDate(d.getDate() + 1);
    }

    html += '</div>'; // .calendar-grid

    $('#dateBar').innerHTML = html;
  }

  function restoreExpanded() {
    var saved = sessionStorage.getItem('vllmExpanded');
    if (!saved) return;
    var shas = JSON.parse(saved);
    if (!Array.isArray(shas)) return;
    shas.forEach(function (sha) {
      var card = document.querySelector('.commit-card[data-sha="' + sha + '"]');
      if (card) card.classList.add('expanded');
    });
  }

  function renderCoverageBar() {
    var el = $('#sideCoverageBar');
    if (!availableDates.length || !analysisDates.length) {
      el.innerHTML = '<span class="coverage-text">No data</span>';
      return;
    }
    var total = availableDates.length;
    var analyzed = 0;
    for (var i = 0; i < availableDates.length; i++) {
      if (analysisDates.indexOf(availableDates[i]) !== -1) {
        analyzed++;
      }
    }
    var pct = Math.round(analyzed / total * 100);
    var missing = total - analyzed;
    var color = missing === 0 ? 'var(--accent)' : (missing < 5 ? 'var(--accent-orange)' : 'var(--accent-red)');
    el.innerHTML = '<span class="coverage-label">Analysis Coverage</span>' +
      '<div class="coverage-track"><div class="coverage-fill" style="width:' + pct + '%;background:' + color + '"></div></div>' +
      '<span class="coverage-text">' + analyzed + '/' + total + ' days</span>';
  }

  function computeModuleHeatmap(commits) {
    if (!commits || !commits.length) return [];
    var counts = {};
    commits.forEach(function (c) {
      if (!c.files) return;
      var seen = {};
      c.files.forEach(function (f) {
        var parts = f.filename.split('/');
        var key;
        if (parts.length >= 3) {
          key = parts[0] + '/' + parts[1] + '/' + parts[2] + '/';
        } else if (parts.length >= 2) {
          key = parts[0] + '/' + parts[1] + '/';
        } else {
          key = parts[0];
        }
        if (!seen[key]) {
          seen[key] = true;
          counts[key] = (counts[key] || 0) + 1;
        }
      });
    });
    var sorted = Object.keys(counts).map(function (k) { return { path: k, count: counts[k] }; });
    sorted.sort(function (a, b) { return b.count - a.count; });
    return sorted.slice(0, 10);
  }

  function renderHeatmap(commits) {
    var el = $('#heatmapSection');
    var bar = $('#heatmapBar');
    var modules = computeModuleHeatmap(commits);
    if (!modules.length) {
      el.style.display = 'none';
      return;
    }
    el.style.display = 'block';
    var maxCount = modules[0].count;
    var html = '';
    for (var i = 0; i < modules.length; i++) {
      var m = modules[i];
      var w = Math.round(m.count / maxCount * 100);
      html += '<div class="heatmap-row"><span class="heatmap-path">' + escapeHtml(m.path) + '</span>' +
        '<div class="heatmap-track"><div class="heatmap-fill" style="width:' + w + '%"></div></div>' +
        '<span class="heatmap-count">' + m.count + '</span></div>';
    }
    bar.innerHTML = html;
  }

  function renderSidebar(commits) {
    renderCoverageBar();
    renderHeatmap(commits);
  }

  function render() {
    if (!commitsData || !commitsData.commits) {
      $('#emptyState').style.display = 'block';
      $('#emptyState').querySelector('.title').textContent = 'No data available';
      $('#emptyState').querySelector('.subtitle').textContent = 'Try selecting a different date or repository';
      $('#commitList').innerHTML = '';
      $('#heatmapSection').style.display = 'none';
      renderStats([]);
      renderSummary();
      renderDateBar();
      return;
    }

    if (commitsData.commits.length === 0) {
      $('#emptyState').style.display = 'block';
      $('#emptyState').querySelector('.title').textContent = '当日无提交';
      $('#emptyState').querySelector('.subtitle').textContent = currentRepo + ' 在 ' + (commitsData.date || availableDates[currentDateIndex]) + ' 没有新的 commit 记录';
      $('#commitList').innerHTML = '';
      $('#heatmapSection').style.display = 'none';
      renderStats([]);
      renderSummary();
      renderDateBar();
      return;
    }

    if (crossDayResults) {
      renderCommitList(crossDayResults.commits, crossDayResults.analysis);
      return;
    }

    $('#emptyState').style.display = 'none';
    renderSummary();
    renderStats(commitsData.commits);
    renderDateBar();
    renderSidebar(commitsData.commits);

    updateFilterChips(commitsData.commits);
    const filtered = filterCommits(commitsData.commits);
    if (filtered.length === 0) {
      if (searchQuery) {
        $('#commitList').innerHTML = '<div class="empty-state"><div class="title">No matching commits today</div><div class="subtitle">Try adjusting your filter or <button class="cross-search-btn" id="crossSearchBtn">Search across all dates</button></div></div>';
      } else {
        $('#commitList').innerHTML = '<div class="empty-state"><div class="title">No matching commits</div><div class="subtitle">Try adjusting your filter or search</div></div>';
      }
      return;
    }

    renderCommitList(filtered, null);
  }

  function renderCommitList(commits, analysisMap) {
    const html = commits.map(function (c) {
      if (analysisMap) {
        var savedAnalysis = analysisData;
        analysisData = { commits: Object.values(analysisMap) };
        var card = renderCommitCard(c);
        analysisData = savedAnalysis;
        return card;
      }
      return renderCommitCard(c);
    }).join('');
    $('#commitList').innerHTML = html;
    restoreExpanded();
  }

  async function searchAcrossDates() {
    if (!searchQuery) return;
    showLoading(true);

    var allCommits = [];
    var allAnalysis = {};
    var dates = availableDates.slice();

    // Fetch in parallel batches of 10
    var batchSize = 10;
    for (var i = 0; i < dates.length; i += batchSize) {
      var batch = dates.slice(i, i + batchSize);
      var results = await Promise.all(batch.map(function (date) {
        return Promise.all([
          fetchJSON(dataUrl(currentRepo, 'commits', date)),
          fetchJSON(dataUrl(currentRepo, 'analysis', date)),
        ]);
      }));
      for (var j = 0; j < results.length; j++) {
        var commitsData = results[j][0];
        var analysisData = results[j][1];
        if (commitsData && commitsData.commits) {
          for (var k = 0; k < commitsData.commits.length; k++) {
            commitsData.commits[k]._date = batch[j];
            allCommits.push(commitsData.commits[k]);
          }
        }
        if (analysisData && analysisData.commits) {
          for (var m = 0; m < analysisData.commits.length; m++) {
            allAnalysis[analysisData.commits[m].sha] = analysisData.commits[m];
          }
        }
      }
    }

    var filtered = filterCommits(allCommits, allAnalysis);
    crossDayResults = { commits: filtered, analysis: allAnalysis };
    showLoading(false);
    render();
  }

  function init() {
    $$('.repo-tab').forEach((tab) => {
      tab.addEventListener('click', async () => {
        $$('.repo-tab').forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        currentRepo = tab.dataset.repo;
        activeFilter = 'all';
        searchQuery = '';
        crossDayResults = null;
        $('#searchInput').value = '';
        $$('.filter-chip').forEach((c) => c.classList.remove('active'));
        $$('.filter-chip')[0].classList.add('active');
        await loadAvailableDates();
        currentDateIndex = 0;
        currentMonth = null;
        if (availableDates.length > 0) {
          await loadDate(availableDates[0]);
        }
      });
    });

    $('#dateBar').addEventListener('click', (e) => {
      const chip = e.target.closest('.calendar-cell:not(.empty)');
      if (chip) {
        const date = chip.dataset.date;
        const idx = availableDates.indexOf(date);
        if (idx !== -1) {
          currentDateIndex = idx;
          var parts = date.split('-');
          currentMonth = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, 1);
          loadDate(date);
        }
        return;
      }
      const prev = e.target.closest('#datePrev');
      if (prev && !prev.disabled) {
        var parts = prev.dataset.offset.split('-');
        currentMonth = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, 1);
        renderDateBar();
        return;
      }
      const next = e.target.closest('#dateNext');
      if (next && !next.disabled) {
        var parts = next.dataset.offset.split('-');
        currentMonth = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, 1);
        renderDateBar();
        return;
      }
    });

    $$('.filter-chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        $$('.filter-chip').forEach((c) => c.classList.remove('active'));
        chip.classList.add('active');
        activeFilter = chip.dataset.filter;
        render();
      });
    });

    $('#searchInput').addEventListener('input', (e) => {
      searchQuery = e.target.value;
      crossDayResults = null;
      render();
    });

    document.addEventListener('click', (e) => {
      const header = e.target.closest('.commit-header');
      if (header) {
        const card = header.closest('.commit-card');
        card.classList.toggle('expanded');
        // Save expanded state to sessionStorage
        var expanded = [];
        document.querySelectorAll('.commit-card.expanded').forEach(function (c) {
          expanded.push(c.dataset.sha);
        });
        sessionStorage.setItem('vllmExpanded', JSON.stringify(expanded));
        return;
      }

      const crossBtn = e.target.closest('#crossSearchBtn');
      if (crossBtn) {
        searchAcrossDates();
        return;
      }

      const toggle = e.target.closest('.diff-toggle');
      if (toggle) {
        const sha = toggle.dataset.sha;
        const content = document.getElementById('diff-' + sha);
        toggle.classList.toggle('open');
        content.classList.toggle('open');
      }
    });

    $('#exportBtn').addEventListener('click', exportToExcel);

    // Set default date range to last 7 days
    var today = cnDateStr(new Date());
    var weekAgo = new Date(today + 'T00:00:00+08:00');
    weekAgo.setDate(weekAgo.getDate() - 6);
    $('#rangeStart').value = cnDateStr(weekAgo);
    $('#rangeEnd').value = today;

    $$('.filter-chip')[0].classList.add('active');
    detectDataBase().then(() => {
      loadAvailableDates().then(() => {
        loadAnalysisDates().then(function () {
          currentDateIndex = 0;
          if (availableDates.length > 0) {
            loadDate(availableDates[0]);
          } else {
            showLoading(false);
            $('#emptyState').style.display = 'block';
          }
        });
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
