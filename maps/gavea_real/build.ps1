# Reconstroi a rede a partir do recorte do OSM e do agrupamento de semaforos.
# Pode rodar de qualquer pasta: .\maps\gavea_real\build.ps1
# Sem --ramps.guess: as "rampas" que ele inventava em ruas urbanas (Artur Araripe, Rodrigo Otavio)
# faziam veiculos ficarem presos sem conseguir trocar de faixa.
Push-Location $PSScriptRoot
try {
    netconvert --osm-files gavea.osm.xml -o gavea_bruta.net.xml --geometry.remove --junctions.join --tls.guess-signals --tls.join --tls.default-type actuated --keep-edges.by-vclass passenger,bus --remove-edges.isolated --keep-edges.in-geo-boundary "-43.2346,-22.9800,-43.2230,-22.9760" --log netconvert.log
    if ($LASTEXITCODE -ne 0) { throw "netconvert falhou no passo 1 (veja netconvert.log)" }
    netconvert --sumo-net-file gavea_bruta.net.xml --node-files tls_grupos.nod.xml -o gavea.net.xml --tls.default-type actuated
    if ($LASTEXITCODE -ne 0) { throw "netconvert falhou no passo 2 (agrupamento dos semaforos)" }
    $n = (Select-String -Path gavea.net.xml -Pattern '<tlLogic ').Count
    Write-Host "Rede gerada: gavea.net.xml com $n controladores de semaforo (esperado: 10)"
    if ($n -ne 10) { Write-Warning "Numero de controladores diferente de 10: confira o tls_grupos.nod.xml" }
}
finally {
    Pop-Location
}
