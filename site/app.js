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

      var headers = ['SHA', 'Date', 'Author', 'Title', 'Tags', 'Ascend Affected', 'Needs Test Update', 'AI Analysis', 'Additions', 'Deletions', 'Files Changed'];
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

  function filterCommits(commits) {
    if (!commits) return [];
    return commits.filter((c) => {
      const a = getAnalysisForSha(c.sha);
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

  function renderSummary() {
    const el = $('#dailySummary');
    if (analysisData && analysisData.daily_summary) {
      el.style.display = 'block';
      $('#summaryText').textContent = analysisData.daily_summary;
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
    html += `<div class="commit-title">${escapeHtml(title)}</div>`;
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
      if (a.comment) {
        html += `<div class="ai-comment"><div class="ai-label">AI Analysis</div>${escapeHtml(a.comment)}</div>`;
      }
      if (a.test_impact) {
        html += `<div class="impact-card test-impact">`;
        html += `<div class="impact-label${a.test_impact.needs_test_update ? ' needs-test' : ''}">${a.test_impact.needs_test_update ? '\u26A0 Test Update Needed' : 'Test Impact'}</div>`;
        html += `<div class="impact-text"><strong>Reason:</strong> ${escapeHtml(a.test_impact.reason || '')}</div>`;
        if (a.test_impact.suggested_test_areas && a.test_impact.suggested_test_areas.length > 0) {
          html += `<div class="impact-text" style="margin-top:4px"><strong>Areas:</strong> ${a.test_impact.suggested_test_areas.map(escapeHtml).join(', ')}</div>`;
        }
        html += `</div>`;
      }
      if (a.ascend_impact) {
        const funcImp = a.ascend_impact.functionality || '';
        const testImp = a.ascend_impact.testing || '';
        if (funcImp !== '无影响' || testImp !== '无影响') {
          html += `<div class="impact-card ascend-impact">`;
          html += `<div class="impact-label ascend">Ascend Impact</div>`;
          if (funcImp && funcImp !== '无影响') {
            html += `<div class="impact-text"><strong>Functionality:</strong> ${escapeHtml(funcImp)}</div>`;
          }
          if (testImp && testImp !== '无影响') {
            html += `<div class="impact-text" style="margin-top:4px"><strong>Testing:</strong> ${escapeHtml(testImp)}</div>`;
          }
          if (a.ascend_impact.needs_test_update) {
            html += `<div class="impact-text" style="margin-top:4px"><span class="stat-badge additions" style="font-size:0.75rem">\u26A0 Test Update Needed</span></div>`;
            if (a.ascend_impact.suggested_test_areas && a.ascend_impact.suggested_test_areas.length > 0) {
              html += `<div class="impact-text" style="margin-top:4px"><strong>Areas:</strong> ${a.ascend_impact.suggested_test_areas.map(escapeHtml).join(', ')}</div>`;
            }
          }
          html += `</div>`;
        }
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

  function renderDateBar() {
    const today = cnDateStr(new Date());
    const days = 14;
    const dates = [];
    const d = new Date(today + 'T00:00:00+08:00');
    const weekdayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    for (let i = 0; i < days; i++) {
      const ds = cnDateStr(d);
      const wd = weekdayNames[d.getDay()];
      dates.push({ date: ds, weekday: wd });
      d.setDate(d.getDate() - 1);
    }
    dates.reverse();

    const currentDate = availableDates[currentDateIndex];
    let html = '';
    for (const { date, weekday } of dates) {
      const hasData = availableDates.includes(date);
      const isActive = date === currentDate;
      let cls = 'date-chip';
      if (hasData) cls += ' has-data';
      if (isActive) cls += ' active';
      html += `<div class="${cls}" data-date="${date}"><span class="chip-weekday">${weekday}</span><span class="chip-date">${date.slice(5)}</span></div>`;
    }
    $('#dateBar').innerHTML = html;

    // Scroll active chip into view
    const active = $('#dateBar .active');
    if (active) active.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
  }

  function render() {
    if (!commitsData || !commitsData.commits || commitsData.commits.length === 0) {
      $('#emptyState').style.display = 'block';
      $('#commitList').innerHTML = '';
      renderStats([]);
      renderSummary();
      renderDateBar();
      return;
    }

    $('#emptyState').style.display = 'none';
    renderSummary();
    renderStats(commitsData.commits);
    renderDateBar();

    const filtered = filterCommits(commitsData.commits);
    if (filtered.length === 0) {
      $('#commitList').innerHTML = `<div class="empty-state"><div class="title">No matching commits</div><div class="subtitle">Try adjusting your filter or search</div></div>`;
      return;
    }

    const html = filtered.map(renderCommitCard).join('');
    $('#commitList').innerHTML = html;
  }

  function init() {
    $$('.repo-tab').forEach((tab) => {
      tab.addEventListener('click', async () => {
        $$('.repo-tab').forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        currentRepo = tab.dataset.repo;
        activeFilter = 'all';
        searchQuery = '';
        $('#searchInput').value = '';
        $$('.filter-chip').forEach((c) => c.classList.remove('active'));
        $$('.filter-chip')[0].classList.add('active');
        await loadAvailableDates();
        currentDateIndex = 0;
        if (availableDates.length > 0) {
          await loadDate(availableDates[0]);
        }
      });
    });

    $('#dateBar').addEventListener('click', (e) => {
      const chip = e.target.closest('.date-chip');
      if (!chip) return;
      const date = chip.dataset.date;
      const idx = availableDates.indexOf(date);
      if (idx !== -1) {
        currentDateIndex = idx;
        loadDate(date);
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
      render();
    });

    document.addEventListener('click', (e) => {
      const header = e.target.closest('.commit-header');
      if (header) {
        const card = header.closest('.commit-card');
        card.classList.toggle('expanded');
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
        currentDateIndex = 0;
        if (availableDates.length > 0) {
          loadDate(availableDates[0]);
        } else {
          showLoading(false);
          $('#emptyState').style.display = 'block';
        }
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
