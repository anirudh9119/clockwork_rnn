import numpy as np

from base import Layer
from utils import random, glorotize, orthogonalize


def recurrent_mask(nclocks, nstates):
	'''
		1 1 1 1 1
		1 1 0 0 0
		0 1 1 0 0
		0 0 1 1 0
		0 0 0 1 1
	'''
	matrix = [np.ones((nstates, nstates * (nclocks)))]
	one_blocks = np.ones((nstates, nstates * (2)))
	matrix.append(np.concatenate([one_blocks, np.zeros((nstates, nstates * (nclocks - 2)))], axis=1))
	for c in range(3, nclocks + 1):
		zero_blocks1 = np.zeros((nstates, nstates * (c - 2)))
		zero_blocks2 = np.zeros((nstates, nstates * (nclocks - c)))	
		matrix.append(np.concatenate([zero_blocks1, one_blocks, zero_blocks2], axis=1))
	mask = np.concatenate(matrix, axis=0)
	print 'number subtract: ', mask.size - mask.sum(), '\n\n\n\n'
	mask = np.ones_like(mask)
	return mask


def make_schedule(periods, nstates):
	sch = []
	for c in periods:
		#for i in range(nstates):
		for i in range(1):
			sch.append(c)
	return sch

class CRNN_HSN(Layer):
	def __init__(self, dinput, nstates, doutput, periods, sigma=0.1, first_layer=False, last_state_only=False):
		'''

			Clockwork Recurrent Neural Network
			This follows the variant described in the paper by Koutnik et al.

			dinput: 
				dimension of the input (per time step)
			
			nstates: 
				number of states per module/clock
			
			doutput: 
				required dimension of the output
			
			periods: 
				the periods of clocks (order is maintained and not sorted)
			
			first_layer:
				True: if this is the first layer of the network. If it is, the gradients w.r.t inputs
						are not calculated as it is useless for training. saves time
				False: gradients w.r.t are calculated and returned
		''' 
		nclocks = len(periods)
		
		Wi = random(nstates, dinput + 1) * sigma
		Wh = random(nclocks * nstates, nclocks * nstates + 1) * sigma
		#Wh = np.zeros((nclocks * nstates, nclocks * nstates + 1))
		#for i in range(nclocks):
		#	for j in range(nclocks):
		#		Wh[i * nstates: (i + 1) * nstates, j * nstates: (j + 1) * nstates] = orthogonalize(random(nstates, nstates))

		Wo = random(doutput, nclocks * nstates + 1) * sigma
		
		H_0 = np.zeros((nclocks * nstates, 1))
	
		mask = recurrent_mask(nclocks, nstates)
		Wh[:,:-1] *= mask

		# column vector to selectively activate rows based on time
		schedules = make_schedule(periods, nstates)
		schedules = np.array(schedules).reshape(-1, 1)
		schedules = 1.0 / schedules

		# store it all
		self.dinput = dinput
		self.nstates = nstates
		self.doutput = doutput
		self.periods = periods
		self.nclocks = nclocks
		self.Wi = Wi
		self.Wh = Wh
		self.Wo = Wo
		self.H_0 = H_0
		self.mask = mask
		self.schedules = schedules
		self.sigma = sigma
		self.first_layer = first_layer
		self.last_state_only = last_state_only

		self.forget()

	def forward(self, X):
		T, n, B = X.shape
		nclocks = self.nclocks
		nstates = self.nstates

		Wi = self.Wi
		Wh = self.Wh
		Wo = self.Wo

		# caches
		inputs, _H_prevs, H_news, Hs, _Hs = {}, {}, {}, {}, {}
		Ys = np.zeros((T, self.doutput, B))

		# H_prev is previous hidden state
		if self.H_last is not None:
			# if we didn't explicitly forget, continue with previous states
			H_prev = self.H_last 
		else:
			H_prev = np.zeros((nclocks * nstates, B))

		actives = []
		for ni in range(nclocks):
			a = np.random.binomial(1, self.schedules[ni], (T, 1))
			a = a.repeat(nstates, axis=1)
			actives.append(a)

		actives = np.concatenate(actives, axis=1)

		for t in xrange(T):
			#active = (((t) % self.schedules) == 0)	# column vector to activate modules
														# for this instant
			active = actives[t:t+1].T
			
			input = np.concatenate([X[t], np.ones((1, B))], axis=0)
			i_h = np.dot(Wi, input)		# input to hidden

			_H_prev = np.concatenate([H_prev, np.ones((1, B))], axis=0) 
			h_h = np.dot(Wh, _H_prev)	# hidden to hidden

			h_new = h_h
			h_new[:nstates] += i_h
			H_new = np.tanh(h_new)
			
			H = active * H_new + (1 - active) * H_prev

			_H = np.concatenate([H, np.ones((1, B))], axis=0)
			y = np.dot(Wo, _H)
			
			Y = np.tanh(y)
			
			# update
			H_prev = H

			# gotta cache em all
			inputs[t] = input; 
			_H_prevs[t] = _H_prev;  
			H_news[t] = H_new;
			Hs[t] = H; 
			_Hs[t] = _H; 
			Ys[t] = Y
		
		self.actives = actives
		self.inputs = inputs
		self._H_prevs = _H_prevs
		self.H_news = H_news
		self.Hs = Hs
		self._Hs = _Hs
		self.Ys = Ys	
		self.H_last = H
		self.T = T
		self.n = n
		self.B = T

		if self.last_state_only:
			return Ys[-1:]
		else:
			return Ys
	
	def backward(self, dY):
		if self.last_state_only:
			last_step_error = dY.copy()
			dY = np.zeros_like(self.Ys)
			dY[-1:] = last_step_error[:]

		T, _, B = dY.shape
		n = self.n
		nclocks = self.nclocks
		nstates = self.nstates
		
		Wi = self.Wi
		Wh = self.Wh
		Wo = self.Wo
		
		dWi = np.zeros_like(Wi)
		dWh = np.zeros_like(Wh)
		dWo = np.zeros_like(Wo)
		dH_prev = np.zeros((nclocks * nstates, B))
		
		if not self.first_layer:
			dX = np.zeros((T, n, B))
		else:
			dX = None

		for t in reversed(xrange(T)):
			#active = (((t) % self.schedules) == 0)
			active = self.actives[t:t+1].T

			input = self.inputs[t]
			_H_prev = self._H_prevs[t]
			H_new= self.H_news[t]
			H = self.Hs[t]
			_H = self._Hs[t]
			Y = self.Ys[t]

			dy = (1.0 - Y ** 2) * dY[t]

			dWo += np.dot(dy, _H.T)
			d_H = np.dot(Wo.T, dy)
			
			dH = d_H[:-1] + dH_prev

			dH_prev = (1 - active) * dH
			
			dH_new = active * dH

			dH_new = (1.0 - H_new ** 2) * dH_new

			di_h = dH_new[:nstates]
			dh_h = dH_new

			dWh += np.dot(dh_h, _H_prev.T)
			dH_prev += np.dot(Wh.T, dh_h)[:-1]
	
			dWi += np.dot(di_h, input.T)

			if not self.first_layer:
				dX[t] = np.dot(Wi.T, di_h)[:-1]


		dWh[:, :-1] *= self.mask

		self.dWi = dWi
		self.dWh = dWh
		self.dWo = dWo

		return dX

	def forget(self):
		self.H_last = None

	def remember(self, state):
		self.H_last = state

	def get_weights(self):
		Wi = self.Wi.flatten()
		Wh = self.Wh.flatten()
		Wo = self.Wo.flatten()
		return np.concatenate([Wi, Wh, Wo])

	def set_weights(self, W):
		i, h = self.Wi.size, self.Wh.size
		Wi, Wh, Wo = np.split(W, [i, i + h])
		self.Wi = Wi.reshape(self.Wi.shape)
		self.Wh = Wh.reshape(self.Wh.shape)
		self.Wo = Wo.reshape(self.Wo.shape)
		
	def get_grads(self):
		dWi = self.dWi.flatten()
		dWh = self.dWh.flatten()
		dWo = self.dWo.flatten()
		return np.concatenate([dWi, dWh, dWo])

	def clear_grads(self):
		self.dWi *= 0
		self.dWh *= 0
		self.dWo *= 0
