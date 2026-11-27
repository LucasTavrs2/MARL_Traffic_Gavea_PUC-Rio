# Constroi a rede do mapa ampliado a partir do recorte do OpenStreetMap (gavea.osm.xml).
# Pode rodar de qualquer pasta: .\maps\gavea_ampliada\build.ps1
# O recorte vem do <bounds> do proprio arquivo; para outro, passe -Recorte "oeste,sul,leste,norte".
# Mesmas opcoes do mapa do MVP (sem --ramps.guess), mais os nomes das ruas na rede.
# Se existir tls_grupos.nod.xml nesta pasta, ele junta semaforos que sao o mesmo cruzamento;
# sem ele, vale o agrupamento automatico do netconvert (confira com scripts\listar_semaforos.py).
param([string]$Recorte = "")
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path gavea.osm.xml)) { throw "Falta o gavea.osm.xml nesta pasta (o recorte do OpenStreetMap)" }
    if (-not $Recorte) {
        $linha = (Select-String -Path gavea.osm.xml -Pattern '<bounds ' | Select-Object -First 1).Line
        if (-not $linha) { throw "O gavea.osm.xml nao tem <bounds>. Passe o recorte: .\build.ps1 -Recorte 'oeste,sul,leste,norte'" }
        $v = @{}
        foreach ($k in 'minlat', 'minlon', 'maxlat', 'maxlon') {
            $v[$k] = [regex]::Match($linha, "$k=""([^""]+)""").Groups[1].Value
        }
        $Recorte = "$($v.minlon),$($v.minlat),$($v.maxlon),$($v.maxlat)"
    }
    Write-Host "Recorte (oeste,sul,leste,norte): $Recorte"
    netconvert --osm-files gavea.osm.xml -o gavea_bruta.net.xml --geometry.remove --junctions.join --tls.guess-signals --tls.join --tls.default-type actuated --keep-edges.by-vclass passenger,bus --remove-edges.isolated --keep-edges.in-geo-boundary $Recorte --output.street-names --log netconvert.log
    if ($LASTEXITCODE -ne 0) { throw "netconvert falhou no passo 1 (veja netconvert.log)" }
    if (Test-Path tls_grupos.nod.xml) {
        netconvert --sumo-net-file gavea_bruta.net.xml --node-files tls_grupos.nod.xml -o gavea.net.xml --tls.default-type actuated
        if ($LASTEXITCODE -ne 0) { throw "netconvert falhou no passo 2 (agrupamento dos semaforos)" }
    }
    else {
        Copy-Item gavea_bruta.net.xml gavea.net.xml -Force
        Write-Host "Sem tls_grupos.nod.xml: usando o agrupamento automatico do netconvert"
    }
    $n = (Select-String -Path gavea.net.xml -Pattern '<tlLogic ').Count
    Write-Host "Rede gerada: gavea.net.xml com $n controladores de semaforo"
}
finally {
    Pop-Location
}
