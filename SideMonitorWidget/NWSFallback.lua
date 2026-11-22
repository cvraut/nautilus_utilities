-- Weather polling, town fallback, and local cache for the NWS feed.
--
-- Flow:
--   * MeasureNWSCurrentObs (WebParser) downloads the DWML feed once on skin load,
--     and afterwards only when this script forces it (every POLL_SECONDS).
--   * On success it calls Run('ok'); on a connect or regex failure it calls
--     Run('fail'). (FinishAction does not fire on failure when the error actions
--     are set, so the status argument is trustworthy.)
--   * Failure walks LOCATIONS in order. If every town fails, the most recent
--     cached record keeps being displayed and we wait for the next poll.
--   * Every successful poll is appended to CACHE_FILE (last MAX_RECORDS kept),
--     so a restart without internet still shows the last good data.
--   * Display values are pushed to skin variables WxTemp/WxHigh/WxLow/WxCond/
--     WxStatus/CurrentObsSource, which the meters read.

local DEBUG = false

local LOCATIONS = {
    { name = "Ann Arbor", lat = "42.279594", lon = "-83.732124" },
    { name = "Ypsilanti", lat = "42.241376", lon = "-83.612350" },
    { name = "Brighton",  lat = "42.530760", lon = "-83.779550" },
    { name = "Detroit",   lat = "42.331429", lon = "-83.045753" },
    { name = "Jackson",   lat = "42.245480", lon = "-84.401860" },
    { name = "Toledo",    lat = "41.663600", lon = "-83.555100" },
}

local URL_TEMPLATE = "https://forecast.weather.gov/MapClick.php?lat=%s&lon=%s&unit=0&lg=english&FcstType=dwml"
local PARENT_MEASURE = "MeasureNWSCurrentObs"
local POLL_SECONDS = 15 * 60       -- one full poll (with fallbacks) every 15 minutes
local STUCK_SECONDS = 5 * 60       -- give up on a fallback chain that never reports back
local MAX_RECORDS = 100
local CACHE_FILE = (os.getenv('APPDATA') or '.') .. '\\Rainmeter\\SideMonitorWidget_weather.csv'

local currentIndex = 1
local chainActive = false   -- true while a poll is walking the fallback towns
local lastAttempt = 0       -- os.time() when the current/last poll started
local lastRecord = nil      -- { time, town, temp, high, low, cond }

function Log(msg)
    if DEBUG then
        SKIN:Bang('!Log', 'NWSFallback: ' .. msg)
    end
end

-- ---------------------------------------------------------------------------
-- Cache file (CSV: epoch,town,temp,high,low,condition)
-- ---------------------------------------------------------------------------

local function ReadCacheLines()
    local lines = {}
    local f = io.open(CACHE_FILE, 'r')
    if f == nil then return lines end
    for line in f:lines() do
        if line ~= '' then table.insert(lines, line) end
    end
    f:close()
    return lines
end

local function ParseRecord(line)
    local t, town, temp, high, low, cond = line:match('^(%d+),([^,]*),([^,]*),([^,]*),([^,]*),(.*)$')
    if t == nil then return nil end
    return { time = tonumber(t), town = town, temp = temp, high = high, low = low, cond = cond }
end

local function Clean(s)
    return (tostring(s or ''):gsub('[,\r\n]', ' '))
end

local function AppendRecord(rec)
    local lines = ReadCacheLines()
    table.insert(lines, table.concat({
        tostring(rec.time), Clean(rec.town), Clean(rec.temp),
        Clean(rec.high), Clean(rec.low), Clean(rec.cond) }, ','))
    while #lines > MAX_RECORDS do table.remove(lines, 1) end
    local f = io.open(CACHE_FILE, 'w')
    if f == nil then
        Log('could not write cache file ' .. CACHE_FILE)
        return
    end
    f:write(table.concat(lines, '\n'), '\n')
    f:close()
end

-- ---------------------------------------------------------------------------
-- Display
-- ---------------------------------------------------------------------------

local function StatusText()
    if lastRecord == nil then
        if chainActive then return 'Fetching weather...' end
        return 'No weather data yet'
    end
    local mins = math.floor((os.time() - lastRecord.time) / 60)
    if mins < 0 then mins = 0 end
    return 'Updated ' .. mins .. ' min ago - ' .. lastRecord.town
end

local function PushStatus()
    SKIN:Bang('!SetVariable', 'WxStatus', StatusText())
end

local function PushRecord()
    if lastRecord ~= nil then
        SKIN:Bang('!SetVariable', 'WxTemp', lastRecord.temp)
        SKIN:Bang('!SetVariable', 'WxHigh', lastRecord.high)
        SKIN:Bang('!SetVariable', 'WxLow', lastRecord.low)
        SKIN:Bang('!SetVariable', 'WxCond', lastRecord.cond)
        SKIN:Bang('!SetVariable', 'CurrentObsSource', lastRecord.town)
    end
    PushStatus()
    SKIN:Bang('!UpdateMeasure', 'MeasureWeatherEmojiIndex')
    SKIN:Bang('!UpdateMeter', '*')
    SKIN:Bang('!Redraw')
end

-- ---------------------------------------------------------------------------
-- Fetching
-- ---------------------------------------------------------------------------

local function UrlFor(idx)
    local loc = LOCATIONS[idx]
    return string.format(URL_TEMPLATE, loc.lat, loc.lon)
end

local function FetchFrom(idx)
    currentIndex = idx
    Log('fetching ' .. LOCATIONS[idx].name)
    SKIN:Bang('!SetOption', PARENT_MEASURE, 'Url', UrlFor(idx))
    SKIN:Bang('!CommandMeasure', PARENT_MEASURE, 'Update')
end

local function StartPoll()
    chainActive = true
    lastAttempt = os.time()
    FetchFrom(1)
end

local function EndPoll()
    chainActive = false
    currentIndex = 1
    -- Leave the measure pointed at the primary town for the next poll.
    SKIN:Bang('!SetOption', PARENT_MEASURE, 'Url', UrlFor(1))
end

local function ReadChild(name)
    local m = SKIN:GetMeasure(name)
    if m == nil then return nil end
    return m:GetStringValue()
end

-- ---------------------------------------------------------------------------
-- Rainmeter entry points
-- ---------------------------------------------------------------------------

function Initialize()
    local lines = ReadCacheLines()
    if #lines > 0 then
        lastRecord = ParseRecord(lines[#lines])
    end
    -- The WebParser measure fetches on its own when the skin loads; that is the
    -- startup poll, so treat it as an active chain starting now.
    currentIndex = 1
    chainActive = true
    lastAttempt = os.time()
    Log('Initialize: cached record ' .. (lastRecord and lastRecord.town or 'none'))
    PushRecord()
end

function Update()
    local now = os.time()
    if chainActive and now - lastAttempt >= STUCK_SECONDS then
        Log('fallback chain never reported back; abandoning it')
        EndPoll()
    end
    if not chainActive and now - lastAttempt >= POLL_SECONDS then
        StartPoll()
    end
    PushStatus()
    return StatusText()
end

-- status: 'ok' from FinishAction, 'fail' from OnConnectErrorAction/OnRegExpErrorAction
function Run(status)
    local town = LOCATIONS[currentIndex].name
    if status == 'ok' then
        SKIN:Bang('!UpdateMeasureGroup', 'NWSChildren')
        local temp = ReadChild('MeasureNWSCurrentTemp')
        local high = ReadChild('MeasureNWSTodayHigh')
        local low  = ReadChild('MeasureNWSTodayLow')
        local cond = ReadChild('MeasureNWSCurrentConditions')
        if tonumber(temp) and tonumber(high) and tonumber(low) then
            Log('success from ' .. town .. ' temp=' .. temp)
            lastRecord = { time = os.time(), town = town, temp = temp,
                           high = high, low = low, cond = cond or '' }
            AppendRecord(lastRecord)
            EndPoll()
            PushRecord()
            return
        end
        Log('parsed but values not numeric from ' .. town)
    else
        Log('fetch failed from ' .. town)
    end

    if currentIndex < #LOCATIONS then
        FetchFrom(currentIndex + 1)
    else
        Log('all towns failed; keeping cached record')
        EndPoll()
        PushRecord()
    end
end
