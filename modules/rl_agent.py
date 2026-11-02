import numpy as np
import os
import warnings

# Suppress Gym/Stable-Baselines warnings for cleaner logs
warnings.filterwarnings('ignore')

try:
    from stable_baselines3 import PPO
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:
    PPO = None
    gym = None

class BorsaEnv(gym.Env if gym else object):
    def __init__(self):
        super(BorsaEnv, self).__init__()
        if gym:
            # State: [Guven_Skoru, Trend_Yonu, Volatilite, Imbalance]
            self.observation_space = spaces.Box(low=-1.0, high=1.0, shape=(4,), dtype=np.float32)
            # Action: 0: Tek Seferde Gir, 1: 2 Kademeli DCA, 2: 3 Kademeli DCA
            self.action_space = spaces.Discrete(3)
        
    def step(self, action):
        # Dummy step
        return np.zeros(4, dtype=np.float32), 0.0, True, False, {}
        
    def reset(self, seed=None, options=None):
        return np.zeros(4, dtype=np.float32), {}

def get_rl_strategy(guven_skoru, yon, volatilite, imbalance):
    """
    RL modelinden işlem stratejisi tavsiyesi alır.
    yon: "LONG" veya "SHORT"
    """
    if PPO is None:
        # Kütüphane yoksa varsayılan strateji dön
        return {
            "dca": "Tek Seferde Tam Miktar Gir (%100)",
            "tp_sl": "Sabit TP/SL Kullan"
        }
        
    model_path = "data/models/rl_ppo_model.zip"
    
    # Basit State
    trend_yon = 1.0 if yon == "LONG" else -1.0
    state = np.array([guven_skoru/100.0, trend_yon, min(1.0, volatilite*10), imbalance], dtype=np.float32)
    
    try:
        if not os.path.exists(model_path):
            # İlk çalışmada basit modeli eğitip kaydet
            env = BorsaEnv()
            model = PPO("MlpPolicy", env, n_steps=128, verbose=0)
            model.learn(total_timesteps=500)
            os.makedirs("data/models", exist_ok=True)
            model.save(model_path)
        else:
            model = PPO.load(model_path)
            
        action, _states = model.predict(state, deterministic=True)
        
        if action == 0:
            dca_strategy = "Tek Seferde Tam Miktar Gir (%100)"
            tp_strategy = "Sabit TP/SL Kullan"
        elif action == 1:
            dca_strategy = "2 Kademeli DCA (%50 Şuan, %50 Düşünce)"
            tp_strategy = "Kısmi Kâr Al (TP1'de %50 çık)"
        else:
            dca_strategy = "3 Kademeli DCA (%30-%30-%40)"
            tp_strategy = "Trailing Stop Kullan (Kârı sür)"
            
        return {
            "dca": dca_strategy,
            "tp_sl": tp_strategy
        }
    except Exception as e:
        print(f"RL Ajanı Hatası: {e}")
        return {
            "dca": "Tek Seferde Tam Miktar Gir (%100)",
            "tp_sl": "Sabit TP/SL Kullan"
        }
