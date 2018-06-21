import numpy as np
import torch
import pickle

class Chain:
    def __init__(self, num_states, up_std=0.1, left_std=1.0):
        self.num_states = num_states
        self.state = 0
        self.action_dim = 2
        self.state_dim = num_states
        self.up_std = up_std
        self.left_std = left_std

    def reset(self):
        self.state = 0
        return self.state

    def step(self, action):
        if action == 0:
            self.state += 1
            reward = np.random.randn() * self.left_std
            done = (self.state == self.num_states)
            if done:
                reward += 10.0
            return self.state, reward, done, None
        elif action == 1:
            return -1, np.random.randn() * self.up_std, True, None

class BaseAgent:
    def eval(self):
        state = self.eval_env.reset()
        total_rewards = 0.0
        while True:
            action = self.act(state, eval=True)
            state, reward, done, _ = self.eval_env.step(action)
            total_rewards += reward
            if done:
                break
        return total_rewards

class QAgent(BaseAgent):
    def __init__(self, env_fn, lr=0.1, epsilon=0.1, discount=1.0):
        self.env = env_fn()
        self.eval_env = env_fn()
        self.q_values = np.zeros((self.env.state_dim, self.env.action_dim))
        self.epsilon = epsilon
        self.discount = discount
        self.lr = lr
        self.total_steps = 0

    def act(self, state, eval=False):
        if not eval and np.random.rand() < self.epsilon:
            return np.random.randint(self.env.action_dim)
        else:
            best_q = np.max(self.q_values[state])
            candidates = [i for i in range(self.env.action_dim) if self.q_values[state, i] == best_q]
            return np.random.choice(candidates)

    def episode(self):
        state = self.env.reset()
        total_rewards = 0.0
        while True:
            action = self.act(state)
            next_state, reward, done, _ = self.env.step(action)
            self.total_steps += 1
            total_rewards += reward

            if done:
                target = 0
            else:
                target = self.discount * np.max(self.q_values[next_state])
            td_error = reward + target - self.q_values[state, action]

            self.q_values[state, action] += self.lr * td_error

            if done:
                break
            state = next_state

        # return total_rewards, self.eval()
        return total_rewards, None

class QuantileAgent(BaseAgent):
    def __init__(self, env_fn, lr=0.1, epsilon=0.1, discount=1.0, num_quantiles=5, mean_exploration=False,
                 active_quantile=-1):
        self.env = env_fn()
        self.eval_env = env_fn()
        self.q_values = np.zeros((self.env.state_dim, self.env.action_dim, num_quantiles))
        self.epsilon = epsilon
        self.discount = discount
        self.lr = lr
        self.num_quantiles = num_quantiles
        self.cumulative_density = (2 * np.arange(num_quantiles) + 1) / (2.0 * num_quantiles)
        self.cumulative_density = torch.FloatTensor(self.cumulative_density)
        self.mean_exploration = mean_exploration
        self.active_quantile = active_quantile
        self.total_steps = 0

    def act(self, state, eval=False):
        if not eval:
            if np.random.rand() < self.epsilon:
                return np.random.randint(self.env.action_dim)
            else:
                if self.mean_exploration:
                    q = self.q_values[state, :, :].mean(-1)
                else:
                    q = self.q_values[state, :, self.active_quantile]
                best_q = np.max(q)
                candidates = [i for i in range(self.env.action_dim) if q[i] == best_q]
                return np.random.choice(candidates)
        q = self.q_values[state].mean(-1)
        best_q = np.max(q)
        candidates = [i for i in range(self.env.action_dim) if q[i] == best_q]
        return np.random.choice(candidates)

    def huber(self, x, k=1.0):
        cond = (x.abs() < k).float().detach()
        return 0.5 * x.pow(2) * cond + k * (x.abs() - 0.5 * k) * (1 - cond)

    def episode(self):
        state = self.env.reset()
        total_rewards = 0.0
        while True:
            action = self.act(state)
            next_state, reward, done, _ = self.env.step(action)
            self.total_steps += 1
            total_rewards += reward

            if done:
                target = np.zeros(self.num_quantiles)
            else:
                q_next = self.q_values[next_state, :].mean(-1)
                a_next = np.argmax(q_next)
                quantiles_next = self.q_values[next_state, a_next, :]
                target = self.discount * quantiles_next
            target += reward

            quantiles = self.q_values[state, action, :]

            quantiles_next = torch.FloatTensor(target).view(-1, 1)
            quantiles = torch.tensor(quantiles.astype(np.float32), requires_grad=True)
            diff = quantiles_next - quantiles
            loss = self.huber(diff) * (self.cumulative_density.view(1, -1) - (diff.detach() < 0).float()).abs()
            loss = loss.mean()
            loss.backward()

            self.q_values[state, action] -= self.lr * quantiles.grad.numpy().flatten()

            if done:
                break
            state = next_state

        # return total_rewards, self.eval()
        return total_rewards, None

def check_optimality(q_values):
    if len(q_values.shape) == 3:
        q_values = q_values.mean(-1)
    assert len(q_values.shape) == 2
    optimal = (q_values[:, 0] - q_values[:, 1]) > 0
    if np.sum(optimal) == len(optimal):
        return True
    return False

def run_episodes(agent, max_steps=1e5):
    ep = 0
    while True:
        online_rewards, eval_rewards = agent.episode()
        ep += 1
        optimal = check_optimality(agent.q_values)
        print('episode %d, return %f, optimal %s' % (ep, online_rewards, optimal))
        if optimal:
            break
        if agent.total_steps > max_steps:
            agent.total_steps = max_steps
            break
    print(agent.total_steps)
    return agent.total_steps

def upper_quantile_chain():
    chain_fns = [lambda: Chain(l, up_std=0, left_std=1.0) for l in np.arange(2, 5)]
    agent_fns = [lambda chain_fn: QAgent(chain_fn),
                 lambda chain_fn: QuantileAgent(chain_fn, active_quantile=-1),
                 lambda chain_fn: QuantileAgent(chain_fn, mean_exploration=True)]
    runs = 30
    total_steps = np.zeros((len(agent_fns, len(chain_fns), runs)))
    for i, agent_fn in enumerate(agent_fns):
        for j, chain_fn in enumerate(chain_fns):
            for r in range(runs):
                agent = agent_fn(chain_fn)
                steps = run_episodes(agent)
                total_steps[i, j, r] = steps
    with open('data/%s.bin' % (upper_quantile_chain.__name__), 'wb') as f:
        pickle.dump(total_steps, f)

if __name__ == '__main__':
    agent = QAgent(lambda :Chain(5, up_std=0, left_std=1.0))
    # agent = QuantileAgent(lambda :Chain(5, up_std=0, left_std=1.0), active_quantile=-1)
    # agent = QuantileAgent(lambda :Chain(5, up_std=0, left_std=1.0), mean_exploration=True)
    # agent = QuantileAgent(lambda :Chain(25, up_std=0.1, left_std=0.2), active_quantile=0)

    # agent = QAgent(lambda :Chain(25, up_std=0.2, left_std=0.1))
    # agent = QuantileAgent(lambda :Chain(25, up_std=0.2, left_std=0.1), active_quantile=0)

    # original quantile dqn
    # agent = QuantileAgent(lambda :Chain(8), mean_exploration=True)
    run_episodes(agent)
