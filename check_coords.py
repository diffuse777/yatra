with open('app.py', encoding='utf-8') as f:
    content = f.read()

# Check if krishnankoil is in CITY_COORDS
if "'krishnankoil'" in content:
    idx = content.find("'krishnankoil'")
    print('Found krishnankoil at line approx:', content[:idx].count('\n') + 1)
    print('Context:', content[idx:idx+60])
else:
    print('krishnankoil NOT found in app.py')

# Check get_travel_info uses CITY_COORDS
if 'CITY_COORDS.get(orig.lower())' in content:
    print('get_travel_info uses CITY_COORDS correctly')
else:
    print('get_travel_info CITY_COORDS usage not found')
