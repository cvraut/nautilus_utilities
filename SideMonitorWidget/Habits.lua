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
-- SKIPS / WORDS: not wired up yet; their rows show "--" and a flat baseline.
--
-- Charts: shared x-axis of 11 points (-10 days .. today). Each series is rescaled
-- on its own: the lowest of its 11 values sits in the bottom bin and the highest
-- in the top bin (10 bins). Days with no data count as 0.

local DATA_FILE = (os.getenv('APPDATA') or '.') .. '\\Rainmeter\\SideMonitorWidget_habits.csv'
local SERIES = { 'commits', 'skips', 'words' }
local LIVE = { commits = true }      -- series that have a data source so far
local DAYS = 11                      -- -10 .. today
local BINS = 10
-- Plot area inside the chart meters in Widget.ini: x 0..225, y 3 (top bin)..25 (bottom bin)
local CHART_W, Y_TOP, Y_BOTTOM = 225, 3, 25
local data = {}                      -- data[series][date] = value
local lastDrawnDay = ''

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

local function Draw(s)
    local name = Cap(s)
    if not LIVE[s] then
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
end

local function DrawAll()
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
-- Rainmeter entry points
-- ---------------------------------------------------------------------------

function Initialize()
    Load()
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
