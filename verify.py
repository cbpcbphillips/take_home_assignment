import atlas

conn = atlas.connect()
atlas.bootstrap(conn)
print("atlas is ready")
conn.close()
