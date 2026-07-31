import requests
from common.config import API_KEY, BASE_URL

# Тестовый player_id (возьми из ошибок выше)
test_player_id = 20100995

print(f"Тестовый запрос для игрока {test_player_id}")
print(f"URL: {BASE_URL}/wotb/tanks/stats/")

response = requests.get(
    f"{BASE_URL}/wotb/tanks/stats/",
    params={
        'application_id': API_KEY,
        'account_id': test_player_id
    },
    timeout=10
)

print(f"HTTP статус: {response.status_code}")
data = response.json()
print(f"API статус: {data.get('status')}")

if data.get('status') == 'ok':
    player_data = data['data'].get(str(test_player_id), [])
    print(f"Количество танков: {len(player_data)}")
    if player_data:
        print(f"Первый танк: {player_data[0]}")
else:
    print(f"Ошибка: {data.get('error')}")