-- Parses nvidia-smi output for the GPU temperature and VRAM rows.
--
-- MeasureGPURaw (RunCommand) runs:
--   nvidia-smi --query-gpu=temperature.gpu,memory.used,memory.total --format=csv,noheader,nounits
-- which prints e.g. "34, 2465, 12288" (deg C, MiB used, MiB total).
-- Its FinishAction calls Parse(), which pushes skin variables:
--   GPUTemp      number 0-100 for the temperature bar
--   GPUTempText  "34" or "--"
--   GPUMemPct    number 0-100 for the VRAM bar
--   GPUMemText   "2.4 G/12.0 G" or "--" (GiB, matching the Mem row's AutoScale)
-- Anything unparseable (driver update, timeout, error text) shows "--" with
-- empty bars; there is no retry loop, the next timer tick simply tries again.

function Initialize()
end

function Update()
    return 0
end

local function SetVars(temp, tempText, memPct, memText)
    SKIN:Bang('!SetVariable', 'GPUTemp', tostring(temp))
    SKIN:Bang('!SetVariable', 'GPUTempText', tempText)
    SKIN:Bang('!SetVariable', 'GPUMemPct', tostring(memPct))
    SKIN:Bang('!SetVariable', 'GPUMemText', memText)
end

function Parse()
    SKIN:Bang('!UpdateMeasure', 'MeasureGPURaw')
    local m = SKIN:GetMeasure('MeasureGPURaw')
    local raw = m and m:GetStringValue() or ''
    local t, used, total = raw:match('^%s*(%d+)%s*,%s*(%d+)%s*,%s*(%d+)')
    t, used, total = tonumber(t), tonumber(used), tonumber(total)
    if t == nil or used == nil or total == nil or total <= 0 then
        SetVars(0, '--', 0, '--')
        return
    end
    SetVars(t, tostring(t), 100 * used / total,
            string.format('%.1f G/%.1f G', used / 1024, total / 1024))
end
