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
local TEMP_MEASURE = "MeasureNWSCurrentTemp"

local currentIndex = 1
local lastGoodTemp = nil
local lastGoodSource = nil
local exhausted = false

function Log(msg)
    if DEBUG then
        SKIN:Bang('!Log', msg)
    end
end

function Initialize()
    currentIndex = 1
    exhausted = false
    Log('NWSFallback: Initialize called, currentIndex=1')
end

function SetUrlTo(idx)
    local loc = LOCATIONS[idx]
    if loc == nil then
        Log('NWSFallback: SetUrlTo got nil location for idx=' .. tostring(idx))
        return
    end
    local url = string.format(URL_TEMPLATE, loc.lat, loc.lon)
    Log('NWSFallback: SetUrlTo idx=' .. idx .. ' name=' .. loc.name .. ' url=' .. url)
    SKIN:Bang('!SetOption', PARENT_MEASURE, 'Url', url)
    SKIN:Bang('!CommandMeasure', PARENT_MEASURE, 'Update')
    Log('NWSFallback: forced update sent for ' .. loc.name)
end

function Update()
    return GetDisplayString()
end

function Run()
    Log('NWSFallback: Run() called, currentIndex=' .. tostring(currentIndex))
    local measure = SKIN:GetMeasure(TEMP_MEASURE)
    if measure == nil then
        Log('NWSFallback: ERROR - could not find measure ' .. TEMP_MEASURE)
        return
    end
    local tempStr = measure:GetStringValue()
    Log('NWSFallback: Run() read tempStr="' .. tostring(tempStr) .. '"')
    local temp = tonumber(tempStr)

    if temp ~= nil then
        Log('NWSFallback: SUCCESS temp=' .. temp .. ' source=' .. LOCATIONS[currentIndex].name)
        lastGoodTemp = temp
        lastGoodSource = LOCATIONS[currentIndex].name
        exhausted = false

        SKIN:Bang('!SetVariable', 'CurrentObsSource', tostring(lastGoodSource))
        SKIN:Bang('!UpdateMeasureGroup', 'NWSChildren')
        SKIN:Bang('!UpdateMeter', '*')
        SKIN:Bang('!Redraw')

        -- Reset the measure's URL back to the primary location (not just the
        -- Lua index) so the NEXT natural UpdateRate tick starts from Ann Arbor
        -- again, rather than silently re-polling whichever fallback succeeded.
        -- This does NOT force an extra fetch right now -- it just rewrites the
        -- option so the measure's own scheduled refresh uses the right URL.
        if currentIndex ~= 1 then
            local primary = LOCATIONS[1]
            local primaryUrl = string.format(URL_TEMPLATE, primary.lat, primary.lon)
            SKIN:Bang('!SetOption', PARENT_MEASURE, 'Url', primaryUrl)
            Log('NWSFallback: reset Url back to primary (' .. primary.name .. ') for next scheduled refresh')
        end

        currentIndex = 1
        return
    end

    Log('NWSFallback: NA/no-match at currentIndex=' .. currentIndex .. ', #LOCATIONS=' .. #LOCATIONS)

    if currentIndex < #LOCATIONS then
        currentIndex = currentIndex + 1
        Log('NWSFallback: advancing to currentIndex=' .. currentIndex)
        SetUrlTo(currentIndex)
    else
        Log('NWSFallback: EXHAUSTED all locations')
        exhausted = true
        SKIN:Bang('!SetVariable', 'CurrentObsSource', 'none')
        SKIN:Bang('!UpdateMeter', '*')
        SKIN:Bang('!Redraw')

        -- Reset URL back to primary so the next scheduled UpdateRate tick
        -- restarts the whole chain from Ann Arbor, instead of resuming
        -- mid-chain from the last fallback tried (e.g. Toledo).
        local primary = LOCATIONS[1]
        local primaryUrl = string.format(URL_TEMPLATE, primary.lat, primary.lon)
        SKIN:Bang('!SetOption', PARENT_MEASURE, 'Url', primaryUrl)

        currentIndex = 1
    end
end

function GetDisplayString()
    if lastGoodTemp ~= nil then
        return tostring(lastGoodTemp)
    elseif exhausted then
        return 'N/A'
    else
        return '...'
    end
end