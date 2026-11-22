-- Parses nvidia-smi output for the GPU temperature and VRAM rows.
--
-- MeasureGPURaw (RunCommand) runs:
--   nvidia-smi --query-gpu=temperature.gpu,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits
-- which prints e.g. "34, 2465, 12288, 2" (deg C, MiB used, MiB total, % load).
-- Its FinishAction calls Parse(), which pushes skin variables:
--   GPUTemp      number 0-100 for the temperature bar
--   GPUTempText  "34" or "--"
--   GPULoad      number 0-100 for the load bar
--   GPULoadText  "2" or "--"
--   GPUMemPct    number 0-100 for the VRAM bar
--   GPUMemText   "2.4 G/12.0 G" or "--" (GiB, matching the Mem row's AutoScale)
-- Anything unparseable (driver update, timeout, error text) shows "--" with
-- empty bars; there is no retry loop, the next timer tick simply tries again.

function Initialize()
end

function Update()
    return 0
end

local function SetVars(temp, tempText, load, loadText, memPct, memText)
    SKIN:Bang('!SetVariable', 'GPUTemp', tostring(temp))
    SKIN:Bang('!SetVariable', 'GPUTempText', tempText)
    SKIN:Bang('!SetVariable', 'GPULoad', tostring(load))
    SKIN:Bang('!SetVariable', 'GPULoadText', loadText)
    SKIN:Bang('!SetVariable', 'GPUMemPct', tostring(memPct))
    SKIN:Bang('!SetVariable', 'GPUMemText', memText)
end

function Parse()
    SKIN:Bang('!UpdateMeasure', 'MeasureGPURaw')
    local m = SKIN:GetMeasure('MeasureGPURaw')
    local raw = m and m:GetStringValue() or ''
    local t, used, total, load = raw:match('^%s*(%d+)%s*,%s*(%d+)%s*,%s*(%d+)%s*,%s*(%d+)')
    t, used, total, load = tonumber(t), tonumber(used), tonumber(total), tonumber(load)
    if t == nil or used == nil or total == nil or load == nil or total <= 0 then
        SetVars(0, '--', 0, '--', 0, '--')
        return
    end
    SetVars(t, tostring(t), load, tostring(load), 100 * used / total,
            string.format('%.1f G/%.1f G', used / 1024, total / 1024))
end
