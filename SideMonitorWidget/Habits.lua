-- Habit tracker: daily values for COMMITS / SKIPS / WORDS and their 10-day charts.
--
-- Storage (all history kept, append-only, written only when a value changes):
--   %APPDATA%\Rainmeter\SideMonitorWidget_habits.csv
--   one line per change: YYYY-MM-DD,series,value     (later lines win)
--
-- COMMITS: daily contribution counts from the public GitHub contribution calendar
--   (MeasureGitHubContrib downloads github.com/users/<user>/contributions). The
--   calendar includes past days, so history is backfilled on the first fetch.
--   Fetched on skin load and then on every 15-minute weather poll (NWSFallback.lua
--   triggers it), so weather and habit data always come from the same tick.
-- SKIPS: daily counts you type into your own published Google Sheet (CSV). Same
--   15-minute fetch; rows sharing a date are summed; blank/missing days count as 0.
-- WORDS: net words added across your tracked docs. A Google Apps Script counts the
--   cloud-reachable files hourly and publishes a "file_id,words,updated" CSV, where
--   file_id = base64 of the doc link/path (so the published feed isn't plaintext-
--   indexable). The widget uses file_id as the per-file key (it'll decode it only
--   when local-file counting needs the real path). It fetches on the 15-min tick,
--   keeps each file's total history in its own DB, and each day's value is the sum
--   over currently-tracked files of (today's total - the previous total). New files
--   baseline at 0; a file dropped from the sheet drops out of the whole line. Rows
--   with a blank count (local files the script can't reach) are ignored for now.
--
-- Charts: shared x-axis of 11 points (-10 days .. today). Each series is rescaled
-- on its own: the lowest of its 11 values sits in the bottom bin and the highest
-- in the top bin (10 bins). Days with no data count as 0.

local DATA_FILE = (os.getenv('APPDATA') or '.') .. '\\Rainmeter\\SideMonitorWidget_habits.csv'
local SERIES = { 'commits', 'skips', 'words' }
local LIVE = { commits = true, skips = true, words = true }   -- series with a data source
local DAYS = 11                      -- -10 .. today
local BINS = 10
-- Plot area inside the chart meters in Widget.ini: x 0..225, y 3 (top bin)..25 (bottom bin)
local CHART_W, Y_TOP, Y_BOTTOM = 225, 3, 25
local data = {}                      -- data[series][date] = value
local lastDrawnDay = ''

-- WORDS keeps per-file total history separately so it can recompute net words and
-- cleanly drop files that leave the tracking sheet.
local WORDS_FILE = (os.getenv('APPDATA') or '.') .. '\\Rainmeter\\SideMonitorWidget_words.csv'
local wordsTotals = {}   -- wordsTotals[name] = { {date=, total=}, ... } chronological
local wordsTracked = {}  -- set of file names in the latest counts fetch
local ComputeWords       -- forward decl; defined in the WORDS section below

local function Today(offsetDays)
    local t = os.date('*t')
    t.hour = 12; t.min = 0; t.sec = 0  -- noon avoids DST edge cases
    return os.date('%Y-%m-%d', os.time(t) - (offsetDays or 0) * 86400)
end

-- ---------------------------------------------------------------------------
-- Storage
-- ---------------------------------------------------------------------------

local function Load()
    for _, s in ipairs(SERIES) do data[s] = {} end
    local f = io.open(DATA_FILE, 'r')
    if f == nil then return end
    for line in f:lines() do
        local d, s, v = line:match('^(%d%d%d%d%-%d%d%-%d%d),(%w+),(%-?%d+)$')
        if d and data[s] then data[s][d] = tonumber(v) end
    end
    f:close()
end

-- changes: list of { date, series, value }; only values that differ are written.
local function Record(changes)
    local lines = {}
    for _, c in ipairs(changes) do
        local d, s, v = c[1], c[2], c[3]
        if data[s][d] ~= v then
            data[s][d] = v
            table.insert(lines, d .. ',' .. s .. ',' .. v)
        end
    end
    if #lines == 0 then return false end
    local f = io.open(DATA_FILE, 'a')
    if f then
        f:write(table.concat(lines, '\n'), '\n')
        f:close()
    end
    return true
end

-- ---------------------------------------------------------------------------
-- Charts
-- ---------------------------------------------------------------------------

local function Cap(s) return s:sub(1, 1):upper() .. s:sub(2) end

-- "On fire" when today beats yesterday: flame image behind the label, label text
-- turns white with a dark shadow (variables read by the label/flame meters).
local function SetFlame(name, hot)
    SKIN:Bang('!SetVariable', name .. 'FlameHidden', hot and '0' or '1')
    SKIN:Bang('!SetVariable', name .. 'LabelColor', hot and '255,255,255,255' or SKIN:GetVariable('ColorBlue'))
    SKIN:Bang('!SetVariable', name .. 'LabelFx', hot and 'Shadow' or 'None')
end

local function Draw(s)
    local name = Cap(s)
    if not LIVE[s] then
        SetFlame(name, false)
        SKIN:Bang('!SetVariable', name .. 'Path', '0,' .. Y_BOTTOM .. ' | LineTo ' .. CHART_W .. ',' .. Y_BOTTOM)
        SKIN:Bang('!SetVariable', name .. 'DotA', '0')
        SKIN:Bang('!SetVariable', name .. 'LineA', '0')   -- no data source: baseline only
        SKIN:Bang('!SetVariable', name .. 'Value', '--')
        return
    end
    local vals, lo, hi = {}, nil, nil
    for i = 1, DAYS do
        local v = data[s][Today(DAYS - i)] or 0
        vals[i] = v
        lo = (lo == nil or v < lo) and v or lo
        hi = (hi == nil or v > hi) and v or hi
    end
    local step = CHART_W / (DAYS - 1)
    local parts, x, y = {}, 0, 0
    for i = 1, DAYS do
        local bin = 0
        if hi > lo then bin = math.floor((vals[i] - lo) / (hi - lo) * (BINS - 1) + 0.5) end
        x = (i - 1) * step
        y = Y_BOTTOM - bin * ((Y_BOTTOM - Y_TOP) / (BINS - 1))
        local pt = string.format('%.1f,%.1f', x, y)
        parts[i] = (i == 1) and pt or ('LineTo ' .. pt)
    end
    SKIN:Bang('!SetVariable', name .. 'Path', table.concat(parts, ' | '))
    SKIN:Bang('!SetVariable', name .. 'DotX', string.format('%.1f', x))
    SKIN:Bang('!SetVariable', name .. 'DotY', string.format('%.1f', y))
    SKIN:Bang('!SetVariable', name .. 'DotA', '255')
    SKIN:Bang('!SetVariable', name .. 'LineA', '255')
    SKIN:Bang('!SetVariable', name .. 'Value', tostring(vals[DAYS]))
    SetFlame(name, vals[DAYS] > vals[DAYS - 1])
end

local function DrawAll()
    if ComputeWords then ComputeWords() end
    for _, s in ipairs(SERIES) do Draw(s) end
    lastDrawnDay = Today()
    SKIN:Bang('!UpdateMeterGroup', 'Habits')
    SKIN:Bang('!Redraw')
end

-- ---------------------------------------------------------------------------
-- GitHub contributions
-- ---------------------------------------------------------------------------

local function ParseContributions(html)
    local dateById, changes = {}, {}
    for d, id in html:gmatch('data%-date="(%d%d%d%d%-%d%d%-%d%d)" id="(contribution%-day%-component%-[%d%-]+)"') do
        dateById[id] = d
    end
    for id, text in html:gmatch('for="(contribution%-day%-component%-[%d%-]+)"[^>]*>([^<]*)</tool%-tip>') do
        local d = dateById[id]
        if d then
            local n = tonumber(text:match('^(%d+) contribution')) or (text:find('^No contribution') and 0)
            if n then table.insert(changes, { d, 'commits', n }) end
        end
    end
    return changes
end

-- ---------------------------------------------------------------------------
-- Skips (your published Google Sheet CSV: header + "YYYY-MM-DD,count" rows)
-- ---------------------------------------------------------------------------

local function ParseSkipsCsv(text)
    local sums, order = {}, {}
    for line in (text .. '\n'):gmatch('([^\r\n]*)[\r\n]') do
        local d, v = line:match('^(%d%d%d%d%-%d%d%-%d%d)%s*,%s*(%d*)')
        if d then
            if sums[d] == nil then order[#order + 1] = d end
            sums[d] = (sums[d] or 0) + (tonumber(v) or 0)   -- blank cell = 0
        end
    end
    local changes = {}
    for _, d in ipairs(order) do changes[#changes + 1] = { d, 'skips', sums[d] } end
    return changes
end

-- ---------------------------------------------------------------------------
-- Words (per-file totals from the published counts CSV: name,words,updated)
-- ---------------------------------------------------------------------------

local function LoadWords()
    wordsTotals = {}
    local f = io.open(WORDS_FILE, 'r')
    if f == nil then return end
    for line in f:lines() do
        local d, name, total = line:match('^(%d%d%d%d%-%d%d%-%d%d),(.-),(%-?%d+)$')
        if d then
            wordsTotals[name] = wordsTotals[name] or {}
            table.insert(wordsTotals[name], { date = d, total = tonumber(total) })
        end
    end
    f:close()
end

local function LastTotal(name)
    local h = wordsTotals[name]
    if h and #h > 0 then return h[#h].total end
    return nil
end

-- append today's total for a file only when it differs from the last recorded one
local function RecordWordsTotal(name, total, date)
    if LastTotal(name) == total then return end
    wordsTotals[name] = wordsTotals[name] or {}
    table.insert(wordsTotals[name], { date = date, total = total })
    local f = io.open(WORDS_FILE, 'a')
    if f then
        f:write(date .. ',' .. (name:gsub('[,\r\n]', ' ')) .. ',' .. total .. '\n')
        f:close()
    end
end

-- most recent recorded total for a file on or before the given date (carry-forward)
local function TotalOnOrBefore(name, date)
    local h = wordsTotals[name]
    if not h then return nil end
    local best = nil
    for _, r in ipairs(h) do
        if r.date <= date then best = r.total else break end
    end
    return best
end

ComputeWords = function()
    data['words'] = data['words'] or {}
    for i = 1, DAYS do
        local day = Today(DAYS - i)
        local prevDay = Today(DAYS - i + 1)   -- calendar day before `day`
        local net = 0
        for name in pairs(wordsTracked) do
            local cur = TotalOnOrBefore(name, day)
            if cur ~= nil then
                local prev = TotalOnOrBefore(name, prevDay)
                if prev == nil then prev = cur end   -- first appearance: baseline, delta 0
                net = net + (cur - prev)
            end
        end
        data['words'][day] = net
    end
end

local function ParseWordsCounts(text)
    local today = Today()
    local tracked, any = {}, false
    for line in (text .. '\n'):gmatch('([^\r\n]*)[\r\n]') do
        local name, words = line:match('^(.-),(%-?%d+),')      -- name,words,updated
        if name == nil then name, words = line:match('^(.-),(%-?%d+)%s*$') end
        if name and name ~= '' and name ~= 'name' then
            tracked[name] = true; any = true
            RecordWordsTotal(name, tonumber(words), today)
        end
    end
    return tracked, any
end

-- ---------------------------------------------------------------------------
-- Rainmeter entry points
-- ---------------------------------------------------------------------------

function Initialize()
    Load()
    LoadWords()
    for name in pairs(wordsTotals) do wordsTracked[name] = true end  -- cached set until first fetch
    DrawAll()
end

function Update()
    if Today() ~= lastDrawnDay then DrawAll() end   -- midnight: shift the charts
    return 0
end

-- status: 'ok' from FinishAction, 'fail' from the error actions (keep old data)
function CommitsFetched(status)
    if status ~= 'ok' then return end
    SKIN:Bang('!UpdateMeasure', 'MeasureGitHubContribText')
    local m = SKIN:GetMeasure('MeasureGitHubContribText')
    local changes = ParseContributions(m and m:GetStringValue() or '')
    if #changes == 0 then return end   -- page format changed or empty: keep old data
    Record(changes)
    DrawAll()
end

-- status: 'ok' from FinishAction, 'fail' from the error actions (keep old data)
function SkipsFetched(status)
    if status ~= 'ok' then return end
    SKIN:Bang('!UpdateMeasure', 'MeasureSkipsSheetText')
    local m = SKIN:GetMeasure('MeasureSkipsSheetText')
    local changes = ParseSkipsCsv(m and m:GetStringValue() or '')
    if #changes == 0 then return end   -- empty/unreachable: keep old data
    Record(changes)
    DrawAll()
end

-- status: 'ok' from FinishAction, 'fail' from the error actions (keep old data)
function WordsFetched(status)
    if status ~= 'ok' then return end
    SKIN:Bang('!UpdateMeasure', 'MeasureWordsCountsText')
    local m = SKIN:GetMeasure('MeasureWordsCountsText')
    local tracked, any = ParseWordsCounts(m and m:GetStringValue() or '')
    if not any then return end        -- empty/unreachable: keep old data
    wordsTracked = tracked
    DrawAll()
end
