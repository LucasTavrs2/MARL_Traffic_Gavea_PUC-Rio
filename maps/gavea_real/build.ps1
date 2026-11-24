# Reconstroi a rede a partir do recorte do OSM e do agrupamento de semaforos.
netconvert --osm-files gavea.osm.xml -o gavea_bruta.net.xml --geometry.remove --ramps.guess --junctions.join --tls.guess-signals --tls.join --tls.default-type actuated --keep-edges.by-vclass passenger,bus --remove-edges.isolated --keep-edges.in-geo-boundary "-43.2346,-22.9800,-43.2230,-22.9760" --log netconvert.log
netconvert --sumo-net-file gavea_bruta.net.xml --node-files tls_grupos.nod.xml -o gavea.net.xml --tls.default-type actuated
