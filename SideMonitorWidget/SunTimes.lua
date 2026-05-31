function Update()
    local riseRaw = SKIN:GetMeasure('MeasureSunriseRaw'):GetStringValue()
    local setRaw  = SKIN:GetMeasure('MeasureSunsetRaw'):GetStringValue()
    local rise = ISOtoMinutes(riseRaw)
    local set  = ISOtoMinutes(setRaw)
    SKIN:Bang('!SetVariable', 'SunriseMinutes', tostring(rise))
    SKIN:Bang('!SetVariable', 'SunsetMinutes',  tostring(set))
    return rise
end

function ISOtoMinutes(isoStr)
    if not isoStr or isoStr == '' then return 0 end
    local h, m = isoStr:match('T(%d+):(%d+):')
    if not h then return 0 end
    local utcMinutes = tonumber(h) * 60 + tonumber(m)
    local offset = GetEasternOffsetMinutes()
    local localMinutes = utcMinutes + offset
    if localMinutes < 0 then localMinutes = localMinutes + 1440 end
    if localMinutes >= 1440 then localMinutes = localMinutes - 1440 end
    return localMinutes
end

function GetEasternOffsetMinutes()
    local d = os.date('*t')
    local dstStart = GetNthSundayOfMonth(d.year, 3, 2)
    local dstEnd   = GetNthSundayOfMonth(d.year, 11, 1)
    if d.yday >= dstStart and d.yday < dstEnd then
        return -4 * 60
    else
        return -5 * 60
    end
end

function GetNthSundayOfMonth(year, month, n)
    local t = os.time({year=year, month=month, day=1, hour=12})
    local firstWday = os.date('*t', t).wday
    local daysUntilSun = (8 - firstWday) % 7
    local dayOfMonth = 1 + daysUntilSun + (n - 1) * 7
    local target = os.time({year=year, month=month, day=dayOfMonth, hour=12})
    return os.date('*t', target).yday
end