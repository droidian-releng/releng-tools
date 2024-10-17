#!/usr/bin/python3
#
# build_changelog - Builds a debian/changelog file from a git commit
# history
# Copyright (C) 2020-2023 Eugenio "g7" Paolantonio <me@medesimo.eu>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright
#      notice, this list of conditions and the following disclaimer in the
#      documentation and/or other materials provided with the distribution.
#    * Neither the name of the <organization> nor the
#      names of its contributors may be used to endorse or promote products
#      derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import os

import sys

import re

import git

import datetime

import email.utils

import argparse

from collections import OrderedDict, namedtuple

CURRENT_ROLLING_SUITE = "trixie"

changelog_entry = namedtuple("ChangelogEntry", ["author", "mail", "contents", "date"])

not_allowed_regex = re.compile("[^a-z0-9_]+")

def none_on_exception(func, *args, **kwargs):
	"""
	Tries to execute a function. If it fails, return None.
	Otherwise, return the function result.

	:param: func: the function to execute
	:param: *args: the args to be passed to the function
	:param: **kwargs: the kwargs to be passed to the function
	"""

	try:
		return func(*args, **kwargs)
	except:
		return None

def sanitize_tag_version(version):
	"""
	Sanitizes a "raw" tag version

	:param: version: the version to sanitize
	"""

	return version.replace("_", "~").replace("%", ":")

def slugify(string):
	"""
	"Slugifies" the supplied string.

	:param: string: the string to slugify
	"""

	return not_allowed_regex.sub(".", string.lower())

def tzinfo_from_offset(offset):
	"""
	Returns a `datetime.timezone` object given
	an offset.

	This based on an answer by 'Turtles Are Cute' on
	stackoverflow: https://stackoverflow.com/a/37097784
	"""

	sign, hours, minutes = re.match('([+\\-]?)(\\d{2})(\\d{2})', str(offset)).groups()
	sign = -1 if sign == '-' else 1
	hours, minutes = int(hours), int(minutes)

	return datetime.timezone(sign * datetime.timedelta(hours=hours, minutes=minutes))

def multiple_replace(string, matches, replacement):
	"""
	Replacement every occourence of the supplied iterable `matches`
	in `string` with replacement (another string).
	"""

	for match in matches:
		string = string.replace(match, replacement)

	return string

class SlimPackage:

	"""
	A debian/changelog generated on-the-fly from the git history
	of the specified repository.
	"""

	DEBIAN_CHANGELOG_TEMPLATE = \
"""%(name)s (%(version)s) %(release)s; urgency=medium

%(content)s

 -- %(author)s <%(mail)s>  %(date)s\n\n"""

	def __init__(self,
		git_repository,
		commit_hash,
		tag=None,
		tag_prefixes=("droidian/",),
		branch=None,
		branch_prefix="feature/",
		rolling_release=None,
		rolling_release_replacement=None,
		comment="release"
	):
		"""
		Initialises the class.

		:param: git_repository: an instance of `git.Repo` for the repository
		:param: commit_hash: the upmost commit hash to look at (most probably
		the commit you want to build)
		:param: tag: the tag specifying the version, or None
		:param: tag_prefixes: the tag prefixes used to find suitable tags, as a tuple.
		Defaults to `("droidian/",)`.
		:param: branch: the branch we're building on, or None
		:param: branch_prefix: the branch prefix used to define feature branches.
		Defaults to `feature/`
		:param: rolling_release: the branch used for rolling releases
		:param: rolling_release_replacement: the actual release to be used when on
		rolling_release
		:param: comment: a comment that will be included in the package version,
		usually the branch slug. Defaults to 'release'

		If `tag` is not specified, the nearest tag is used instead. If no tag
		is found, the latest version of an eventual, old debian/changelog is
		used instead. If no debian/changelog exist, the starting base version will
		be "0.0.0".
		"""

		self.git_repository = git_repository
		self.commit_hash = commit_hash
		self.tag = tag
		self.tag_prefixes = tag_prefixes
		self.branch = branch
		self.branch_prefix = branch_prefix
		self.rolling_release = rolling_release
		self.rolling_release_replacement = rolling_release_replacement
		self.comment = slugify(comment.replace(self.branch_prefix, ""))

		self._name = None
		self._is_native = None
		self._version = None
		self._release = None

		self.tags = {
			tag.commit.hexsha : tag.name
			for tag in self.git_repository.tags
			if tag.name.startswith(self.tag_prefixes) or tag.name.startswith("upstream/")
		}

		hint_file = os.path.join(self.git_repository.working_dir, "debian/droidian-version-hint")
		if os.path.exists(hint_file):
			with open(hint_file, "r") as f:
				self.version_hint = f.read().strip() or None
		else:
			self.version_hint = None

	def get_version_from_non_native_tags(self):
		"""
		Returns a suitable version for non-native packages, looking
		at the nearest tags and taking in account eventual epochs.
		"""

		tags = [
			self.tags[k.hexsha]
			for k in self.git_repository.iter_commits(rev=self.commit_hash)
			if k.hexsha in self.tags
		] + [self.version_hint]

		latest_upstream = None
		for tag in tags:
			if tag and tag.startswith(self.tag_prefixes):
				# Explicit version, return
				sanitized = multiple_replace(tag, self.tag_prefixes, "").split("/")[-1]
				if latest_upstream is None:
					return sanitized
				else:
					# Latest upstream set (see below).
					# Extract epoch, if any, then return the new version
					if "%" in sanitized:
						return "%s:%s" % (sanitized.split("%")[0], latest_upstream)
					else:
						return latest_upstream
			elif tag and tag.startswith("upstream/") and latest_upstream is None:
				# Upstream tag. If we're here, this is probably the nearest.
				# We can't go ahead since we need to determine if there
				# is an epoch in the debian version.
				# Set latest_upstream so that it can be handled on
				# the next tag_prefixes check.
				latest_upstream = tag.replace("upstream/","")
				continue

		if latest_upstream is not None:
			# Handle cases where upstream/ is present but a downstream
			# tag isn't
			return latest_upstream

		return None

	def get_version_from_changelog(self):
		"""
		Returns the latest version from debian/changelog, or None
		if nothing has been found.
		"""

		_changelog_path = os.path.join(self.git_repository.working_dir, "debian/changelog")
		if os.path.exists(_changelog_path):
			with open(_changelog_path, "r") as f:
				try:
					return f.readline().split(" ")[1][1:-1]
				except:
					pass

		return None

	@property
	def name(self):
		"""
		Returns the source package name.
		"""

		if self._name is None:
			# Retrieve the source package name from debian/control
			_control_path = os.path.join(self.git_repository.working_dir, "debian/control")

			if os.path.exists(_control_path):
				with open(_control_path, "r") as f:
					# Search for the source definition
					for line in f:
						if line.startswith("Source: "):
							# Here we go!
							self._name = line.strip().split(" ", 1)[-1]
							break

					if self._name is None:
						raise Exception("Unable to determine the source package name!")
			else:
				raise Exception("Unable to find debian/control")

		return self._name

	@property
	def is_native(self):
		"""
		Returns True if the source package is native, False if not.
		"""

		if self._is_native is None:
			# Check debian/source/format
			_source_format_path = os.path.join(self.git_repository.working_dir, "debian/source/format")

			if os.path.exists(_source_format_path):
				with open(_source_format_path, "r") as f:
					_format = f.read().strip()
					self._is_native = not (_format == "3.0 (quilt)")
			else:
				raise Exception("Unable to find debian/source/format")

		return self._is_native

	@property
	def version(self):
		"""
		Returns the package version.

		Version template:
		    %(starting_version)s(+|~)git%(timestamp)s.%(short_commit).%(comment)

		If a tag has been specified, that will be used as the `starting_version`.
		Otherwise, the nearest tag is used. If no tag is found and an old
		`debian/changelog` file exists, the starting_version is read from there.
		Failing that, it defaults to "0.0.0".
		"""

		if self._version is not None:
			# Return right now to avoid defining strategies again
			return self._version

		# There are a bunch of strategies to try to get an accurate version.
		# These are tried top-bottom, and the first one to return a
		# string wins.
		_starting_version_strategies = [
			# If we have a tag (i.e. production builds), use directly that,
			# as the version is specified there.
			lambda: multiple_replace(self.tag, self.tag_prefixes, "").split("/")[-1] if self.tag is not None else None,

			# On non-native packages, search for the nearest tag between
			# those starting with upstream/ and tag_prefixes (these are
			# already filtered in self.tag in this class' __init__).
			#  - If the nearest tag starts with upstream/, this is a version
			#    bump so the version_template must be changed accordingly
			#    (see below)
			#  - If the nearest tag starts with the tag_prefixes, this is
			#    simply another debian revision, so the old revision
			#    is already specified.
			lambda: none_on_exception(self.get_version_from_non_native_tags) if not self.is_native else None,

			# Get the nearest tag starting with tag_prefixes using git describe
			lambda: none_on_exception(
				lambda x, y: multiple_replace(x.git.describe("--tags", "--always", "--abbrev=0", *["--match=%s*" % z for z in y]),y,"").split("/")[1],
				self.git_repository,
				self.tag_prefixes
			),

			# Open an eventual debian/changelog and try to pick up the
			# starting version from there
			self.get_version_from_changelog,

			# Finally, fallback to 0.0.0
			lambda: "0.0.0"
		]

		starting_version = None
		for strategy in _starting_version_strategies:
			starting_version = strategy()

			if starting_version is not None:
				break

		if not self.is_native and not "-" in starting_version:
			# Non-native package, but version has not yet a debian revision
			# This means that we probably have picked up the version
			# from an "upstream/" tag, so we should add "-1" manually
			# since there is not a debian release yet -- and also
			# switch to ~ rather than + since we're going to use
			# the new version
			version_template = "%s-1~git%s"
		else:
			# Using the old version as base. If the package is non-native,
			# the old revision has been already picked up so don't
			# really worry about that
			version_template = "%s+git%s"

		self._version = version_template % (
			starting_version,
			".".join(
				[
					datetime.datetime.fromtimestamp(
						self.git_repository.commit(rev=self.commit_hash).committed_date
					).strftime("%Y%m%d%H%M%S"),
					self.commit_hash[0:7],
					self.comment
				]
			)
		)

		if not self.is_native and not "-" in self._version:
			# This could only happen when a version for a non-native package
			# has been tagged without specifying the debian revision
			raise Exception("Non native package but no debian revision specified while tagging!")

		return self._version

	@property
	def release(self):
		"""
		Returns the target release.
		"""

		if not self._release and self.tag is not None:
			self._release = multiple_replace(self.tag, self.tag_prefixes, "").split("/")[0]
		elif not self._release and self.branch is not None:
			self._release = self.branch.replace(self.branch_prefix, "").split("/")[0]
		elif not self._release:
			raise Exception("At least one between tag and branch must be specified")

		if \
			self.rolling_release is not None and \
			self.rolling_release_replacement is not None and \
			self._release == self.rolling_release:
				self._release = self.rolling_release_replacement

		return self._release

	def iter_changelog(self):
		"""
		Returns a formatted changelog
		"""

		# Keep track of every tag with our prefix
		tags = {
			hexsha : multiple_replace(tag_name, self.tag_prefixes, "")
			for hexsha, tag_name in self.tags.items()
			if tag_name.startswith(self.tag_prefixes)
		}

		# Use the current release/version pair as the top version
		nearest_version = "%s/%s" % (self.release, self.version)

		entries = OrderedDict()

		####
		entry = None
		for commit in self.git_repository.iter_commits(rev=self.commit_hash):

			# On shallow clones, the last commit actually has a parent,
			# but we're unable to access it.
			# Use this information to determine if we should stop
			# here
			if commit.parents:
				try:
					commit.parents[0].parents
				except ValueError:
					last_commit = True
				else:
					last_commit = False
			else:
				last_commit = True

			if (commit.hexsha in tags and not commit.hexsha == self.commit_hash) \
				or last_commit:

				# new version, or root commit, should yield the previous
				release, version = nearest_version.split("/")

				# Store the commit if this is the last one
				if last_commit:
					if entry is None:
						# This is an edge case, but I'm not a fan of
						# repeating code - need to do something better here
						entry = changelog_entry(
							author=commit.author.name,
							mail=commit.author.email,
							date=email.utils.format_datetime(
								git.objects.util.from_timestamp(
									commit.committed_date,
									commit.committer_tz_offset
								)
							),
							contents=OrderedDict()
						)

					entry.contents.setdefault(
						commit.author.name,
						[]
					).insert(
						0,
						commit.message.split("\n")[0] # Pick up only the first line
					)

				# Get number of authors
				authors = len(entry.contents)

				yield (
					self.DEBIAN_CHANGELOG_TEMPLATE % {
						"name" : self.name,
						"version" : sanitize_tag_version(version),
						"release" : release,
						"content" : "\n\n".join(
							[
								("  [ %(author)s ]\n%(messages)s" if authors > 1 else "%(messages)s") % {
									"author" : author,
									"messages" : "\n".join(
										[
											"  * %s" % message
											for message in messages
										]
									)
								}
								for author, messages in entry.contents.items()
							]
						),
						"author" : entry.author,
						"mail" : entry.mail,
						"date" : entry.date
					}
				)

				# Reset entry
				entry = None

				# If we should change version, do that
				if not last_commit:
					nearest_version = tags[commit.hexsha]
				else:
					break

			# Create entry if we should
			if entry is None:
				entry = changelog_entry(
					author=commit.author.name,
					mail=commit.author.email,
					date=email.utils.format_datetime(
						git.objects.util.from_timestamp(
							commit.committed_date,
							commit.committer_tz_offset
						)
					),
					contents=OrderedDict()
				)

			# Add commit details to the entry
			entry.contents.setdefault(
				commit.author.name,
				[]
			).insert(
				0,
				commit.message.split("\n")[0] # Pick up only the first line
			)

parser = argparse.ArgumentParser(description="Builds a debian/changelog file from a git history tree")
parser.add_argument(
	"--commit",
	type=str,
	help="the commit to search from. Defaults to the current HEAD"
)
parser.add_argument(
	"--git-repository",
	type=str,
	default=os.getcwd(),
	help="the git repository to search on. Defaults to the current directory"
)
parser.add_argument(
	"--tag",
	type=str,
	help="the eventual tag that specifies the base version of the package"
)
parser.add_argument(
	"--tag-prefix",
	type=str,
	nargs="+",
	default=["droidian/", "hybris-mobian/"],
	help="the prefix of the tag supplied with --tag. Defaults to droidian/."
)
parser.add_argument(
	"--branch",
	type=str,
	help="the branch where the commit is on. Defaults to the current branch"
)
parser.add_argument(
	"--branch-prefix",
	type=str,
	default="feature/",
	help="the prefix of the branch supplied with --branch. Defaults to feature/"
)
parser.add_argument(
	"--rolling-release",
	type=str,
	default="droidian",
	help="the branch used for rolling releases. Defaults to droidian"
)
parser.add_argument(
	"--rolling-release-replacement",
	type=str,
	default=CURRENT_ROLLING_SUITE,
	help="the actual release that is going to be used on rolling releases. Defaults to %s" % CURRENT_ROLLING_SUITE
)
parser.add_argument(
	"--comment",
	type=str,
	default="release",
	help="a slugified comment that is set as version suffix. Defaults to release"
)

if __name__ == "__main__":
	args = parser.parse_args()

	try:
		repository = git.Repo(args.git_repository, odbt=git.GitCmdObjectDB)
	except:
		raise Exception(
			"Unable to load git repository at %s. You can use --git-repository to change the repo path" % \
				args.git_repository
		)

	pkg = SlimPackage(
		repository,
		commit_hash=args.commit or repository.head.commit.hexsha,
		tag=args.tag,
		tag_prefixes=tuple(args.tag_prefix),
		branch=args.branch or (None if args.tag else repository.active_branch.name),
		branch_prefix=args.branch_prefix,
		rolling_release=args.rolling_release,
		rolling_release_replacement=args.rolling_release_replacement,
		comment=args.comment
	)

	# Build a version right now, so that we don't worry about (eventually)
	# replacing debian/changelog before the get_version_from_changelog
	# strategy is executed
	version = pkg.version
	print("I: Resulting version is %s" % version)

	with open("debian/changelog", "w") as f:
		for entry in pkg.iter_changelog():
			f.write(entry)


